"""One active voice call; generation IDs fence all late events and acknowledgements."""
import asyncio
import base64
import time
from contextlib import aclosing
from dataclasses import dataclass, field

from pipeline.native import clauses
from pipeline.admission import InsufficientMemory
from turn.endpoint import Endpointer
from connect.workflow import BookingWorkflow
from connect.telephony import DTMFDetector
from connect.domain import ASR_HINT

SYSTEM = ("You are EYWA, a local voice assistant in a voice pipeline demonstration. "
          "Reply in one or two short natural sentences, without markdown. "
          "You have no tools yet: do not claim to book, change, or verify anything. "
          "Ask for clarification when needed.")


def memory_refusal(error):
    if isinstance(error, InsufficientMemory):
        return error
    for child in getattr(error, "exceptions", ()):
        if found := memory_refusal(child):
            return found
    return None


@dataclass
class Reply:
    turn_id: int
    clauses: dict[int, str] = field(default_factory=dict)
    acknowledged: int = -1
    played: list[str] = field(default_factory=list)
    generated: bool = False
    challenge: str = ""


class VoiceSession:
    def __init__(self, runtime, send, workflow: BookingWorkflow | None = None):
        self.runtime, self.send = runtime, send
        self.endpoint = Endpointer()
        self.generation = 0
        self.reply: Reply | None = None
        self.task: asyncio.Task | None = None
        self.history: list[dict[str, str]] = []
        self.credit = asyncio.Event()
        self.credit.set()
        self.closed = False
        self.workflow = workflow
        self.dtmf = DTMFDetector() if workflow else None

    def keep_played(self):
        if self.reply and self.reply.played:
            self.history.append({"role": "assistant", "content": " ".join(self.reply.played)})
        self.reply = None
        self.prune_history()

    def prune_history(self):
        self.history = self.history[-12:]
        while sum(len(m["content"]) for m in self.history) > 6000:
            self.history.pop(0)
        # Start at a user turn rather than an orphaned assistant message.
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)

    async def interrupt(self):
        old = self.generation
        self.generation += 1  # fence first, before awaiting cancellation or I/O
        started = time.perf_counter()
        was_running = self.task is not None and not self.task.done()
        if was_running:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        if self.workflow:
            self.workflow.interrupted()
        self.keep_played()
        self.credit.set()
        if not self.closed:
            if prepare := getattr(self.runtime.tts, "prepare", None):
                prepare()
            await self.send({"type": "interrupted", "turn_id": old,
                "cancelled_generation": was_running,
                "server_cancel_ms": (time.perf_counter() - started) * 1000})

    async def acknowledge(self, turn_id: int, clause_id: int):
        reply = self.reply
        if (reply is None or turn_id != self.generation or reply.turn_id != turn_id
                or clause_id != reply.acknowledged + 1 or clause_id not in reply.clauses):
            return
        reply.played.append(reply.clauses.pop(clause_id))
        reply.acknowledged = clause_id
        if self.workflow and reply.challenge:
            self.workflow.played(reply.challenge)
        self.credit.set()

    async def feed(self, pcm: bytes):
        if self.dtmf and (digit := self.dtmf.feed(pcm)):
            await self.control_digit(digit)
            return
        probability = self.runtime.vad.probability(pcm)
        event = self.endpoint.feed(pcm, probability)
        if event.onset:
            await self.interrupt()
            await self.send({"type": "speech_started", "turn_id": self.generation,
                "first_speech_frame": self.endpoint.clock_ms // 32 - (self.endpoint.config.onset_ms + 31) // 32 + 1})
        if event.pcm:
            await self.interrupt()
            turn_id = self.generation
            self.reply = Reply(turn_id)
            delay = event.endpoint_ms - event.speech_end_ms
            self.task = asyncio.create_task(self.respond(event.pcm, turn_id, delay, event.speech_end_ms // 32))

    async def control_digit(self, digit):
        if digit not in "10":
            return
        await self.interrupt()
        self.endpoint.reset()
        self.reply = Reply(self.generation)
        self.task = asyncio.create_task(self.respond(b"", self.generation, 0, text_override="yes" if digit == "1" else "no"))

    async def respond(self, pcm: bytes, turn_id: int, endpoint_delay_ms: int, speech_end_frame: int = 0, text_override=None):
        tracing = self.runtime.tracing
        metrics = {"vad_endpoint_ms": endpoint_delay_ms, "asr_audio_s": len(pcm) / 32000}
        metrics["speech_end_frame"] = speech_end_frame
        started = time.perf_counter()
        try:
            async with asyncio.timeout(90):
                with tracing.span("turn", turn_id):
                    with tracing.span("vad.endpoint", turn_id) as span:
                        span.set_attribute("endpoint.delay_ms", endpoint_delay_ms)
                    await self.send({"type": "state", "state": "transcribing", "turn_id": turn_id})
                    t = time.perf_counter()
                    with tracing.span("asr", turn_id):
                        if text_override is not None:
                            text = text_override
                        elif self.workflow:
                            text = await self.runtime.asr.transcribe(pcm, prompt=ASR_HINT)
                        else:
                            text = await self.runtime.asr.transcribe(pcm)
                    metrics["asr_ms"] = (time.perf_counter() - t) * 1000
                    metrics["asr_rtf"] = metrics["asr_ms"] / 1000 / metrics["asr_audio_s"] if pcm else None
                    if turn_id != self.generation or not text:
                        await self.send({"type": "empty_speech", "turn_id": turn_id})
                        return
                    self.history.append({"role": "user", "content": text[:2000]})
                    self.prune_history()
                    await self.send({"type": "transcript", "role": "user", "text": text, "turn_id": turn_id})
                    queue = asyncio.Queue(maxsize=3)

                    async def produce():
                        t = time.perf_counter()
                        tokens = 0
                        if self.workflow:
                            with tracing.span("workflow", turn_id):
                                outcome = await self.workflow.handle(text, tracing, turn_id)
                            self.reply.challenge = outcome.challenge
                            await self.send({"type": "booking_state", "turn_id": turn_id,
                                "state": outcome.state, "success": outcome.success, "booking": outcome.booking,
                                "draft": self.workflow.draft, "readback_played": self.workflow.heard})
                            await queue.put(outcome.text)
                            await queue.put(None)
                            metrics["tools_ms"] = (time.perf_counter() - t) * 1000
                            return

                        async def measured_tokens():
                            nonlocal tokens
                            async with aclosing(self.runtime.llm.stream([
                                {"role": "system", "content": SYSTEM}, *self.history])) as stream:
                                async for token in stream:
                                    if tokens == 0:
                                        metrics["llm_ttft_ms"] = (time.perf_counter() - t) * 1000
                                    tokens += 1
                                    yield token
                        with tracing.span("llm", turn_id):
                            async with aclosing(clauses(measured_tokens())) as stream:
                                async for clause in stream:
                                    await queue.put(clause)
                        metrics["llm_ms"] = (time.perf_counter() - t) * 1000
                        metrics["llm_content_events"] = tokens
                        timings = getattr(self.runtime.llm, "timings", {})
                        if timings.get("predicted_per_second") is not None:
                            metrics["llm_tokens_per_second"] = timings["predicted_per_second"]
                            metrics["llm_generated_tokens"] = timings["predicted_n"]
                        await queue.put(None)

                    async def speak():
                        clause_id = 0
                        with tracing.span("tts", turn_id):
                            while (clause := await queue.get()) is not None:
                                # Bound audio queued ahead of confirmed playback.
                                while len(self.reply.clauses) >= 2:
                                    self.credit.clear()
                                    await self.credit.wait()
                                tts_started = time.perf_counter()
                                had_audio = False
                                async with aclosing(self.runtime.tts.stream(clause)) as stream:
                                    async for rate, audio in stream:
                                        had_audio = had_audio or bool(audio)
                                        if turn_id != self.generation:
                                            return
                                        if "tts_first_chunk_ms" not in metrics:
                                            metrics["tts_first_chunk_ms"] = (time.perf_counter() - tts_started) * 1000
                                            metrics["server_voice_to_voice_ms"] = endpoint_delay_ms + (time.perf_counter() - started) * 1000
                                        await self.send({"type": "audio", "turn_id": turn_id,
                                            "clause_id": clause_id, "sample_rate": rate,
                                            "pcm": base64.b64encode(audio).decode()})
                                if not had_audio:
                                    raise RuntimeError("TTS produced no audio")
                                self.reply.clauses[clause_id] = clause
                                await self.send({"type": "clause_end", "turn_id": turn_id,
                                    "clause_id": clause_id, "text": clause})
                                clause_id += 1
                    async with asyncio.TaskGroup() as group:
                        group.create_task(produce())
                        group.create_task(speak())
                    self.reply.generated = True
                    await self.send({"type": "response_done", "turn_id": turn_id, "metrics": metrics})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Avoid persisting provider exception strings: they may contain speech.
            refusal = memory_refusal(exc)
            await self.send({"type": "error", "turn_id": turn_id,
                "message": str(refusal) if refusal else "Voice pipeline failed; retry or restart the call.",
                "error_class": type(refusal).__name__ if refusal else type(exc).__name__})

    async def close(self):
        self.closed = True
        await self.interrupt()
        self.history.clear()
        self.endpoint.reset()
