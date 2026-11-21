"""Sequential real-audio evaluation, sharing resident models with the application.

Transport is an in-process client of the production VoiceSession, not a browser.
PCM is paced in real time. Generated audio is consumed by the synthetic caller;
only fully received clauses are acknowledged. Planned interruptions stop that
consumer and exercise the same cancellation control as the browser.
"""
import asyncio
import base64
import hashlib
import json
import re
import sqlite3
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from connect.domain import grounded_fields, today, ASR_HINT
from connect.store import BookingStore
from connect.telephony import PhoneCodec, tone
from connect.workflow import BookingWorkflow
from eval.audio import caller_audio
from server.session import VoiceSession

ROOT = Path(__file__).resolve().parents[1]


def percentile(values):
    values = [v for v in values if v is not None]
    return {"n": len(values), "p50": float(np.percentile(values, 50)) if values else None,
        "p95": float(np.percentile(values, 95)) if values else None}


def redact(text):
    text = re.sub(r"\b(?:\+?\d[\d ()-]{8,}\d)\b", "[REDACTED_NUMBER]", text)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.\w+\b", "[REDACTED_EMAIL]", text)
    return re.sub(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b", "[REDACTED_DATE]", text)


class FaultInjectingStore(BookingStore):
    def write(self, *args, **kwargs):
        raise sqlite3.OperationalError("Injected commit failure")


class SyntheticClient:
    def __init__(self, runtime, workflow, phone=False):
        self.events = asyncio.Queue()
        self.session = VoiceSession(runtime, self.events.put, workflow)
        self.session.runtime.vad.reset()
        self.in_codec = PhoneCodec() if phone else None
        self.out_codec = PhoneCodec() if phone else None
        self.trace, self.metrics, self.transcripts = [], [], []
        self.claims = self.false_claims = 0
        self.audio = []
        self.blocked_through = -1
        self.barge = []

    async def feed(self, pcm, planned_interrupt=False):
        pcm += b"\0" * (-len(pcm) % 1024) + b"\0" * 1024 * 20
        if self.in_codec:
            pcm = self.in_codec.process(pcm)
        self.audio.append(pcm)
        next_frame = time.perf_counter()
        loud = 0
        onset = None
        for offset in range(0, len(pcm), 1024):
            await asyncio.sleep(max(0, next_frame - time.perf_counter()))
            frame = pcm[offset:offset + 1024]
            rms = float(np.sqrt(np.mean((np.frombuffer(frame, dtype="<i2").astype(float) / 32768) ** 2)))
            loud = loud + 1 if rms > 0.025 else 0
            if planned_interrupt and rms > 0.025 and onset is None:
                onset = time.perf_counter()
            if planned_interrupt and loud >= 2:
                stopped = time.perf_counter()
                self.blocked_through = self.session.generation
                old_task = self.session.task
                await self.session.interrupt()
                self.barge.append({"stop_dispatch_ms": (stopped - onset) * 1000,
                    "onset_frame_uncertainty_ms": 32,
                    "generation_fenced": self.session.generation > self.blocked_through and (old_task is None or old_task.done())})
                planned_interrupt = False
            await self.session.feed(frame)
            next_frame += 0.032

    async def receive(self, until_audio=False):
        async with asyncio.timeout(100):
            while True:
                event = await self.events.get()
                kind = event["type"]
                if kind == "error":
                    raise RuntimeError(event["message"])
                if event.get("turn_id", self.session.generation) <= self.blocked_through:
                    continue
                if kind == "transcript":
                    self.transcripts.append(event["text"])
                    self.trace.append({"role": "caller", "text": redact(event["text"])})
                if kind == "audio":
                    pcm = base64.b64decode(event["pcm"])
                    rate = event["sample_rate"]
                    if self.out_codec:
                        pcm, rate = self.out_codec.process(pcm, rate), 16000
                    values = np.frombuffer(pcm, dtype="<i2")
                    length = round(len(values) * 16000 / rate)
                    values = np.interp(np.arange(length) * rate / 16000, np.arange(len(values)), values).astype("<i2")
                    self.audio.append(values.tobytes())
                    if until_audio:
                        return
                if kind == "clause_end":
                    self.trace.append({"role": "agent", "text": redact(event["text"])})
                    if re.search(r"Your appointment is (confirmed|cancelled)", event["text"]):
                        self.claims += 1
                        result = self.session.workflow.result
                        rows = self.session.workflow.store.list(self.session.workflow.caller)
                        if not result or not any(row == result for row in rows):
                            self.false_claims += 1
                    await self.session.acknowledge(event["turn_id"], event["clause_id"])
                if kind == "response_done":
                    self.metrics.append(event["metrics"])
                    return


async def run_suite(runtime, destination: Path, seed=42, limit=None, progress=None, reference_date=None, scenario_ids=None):
    from benchmarks.phase0.run_phase0 import metadata, wer
    from benchmarks.phase1.run_live import source_hash
    suite_path = ROOT / "eval/scenarios/suite.json"
    suite = json.loads(suite_path.read_text())
    reference_date = reference_date or today()
    scenarios = [(i,s) for i,s in enumerate(suite["scenarios"]) if scenario_ids is None or s["id"] in scenario_ids][:limit]
    if not scenarios:
        raise ValueError("No matching scenarios")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = destination / run_id
    directory.mkdir(parents=True, exist_ok=True)
    report = {"id": run_id, "status": "running", "mode": runtime.mode,
        "metadata": metadata(), "source_sha256": source_hash(), "seed": seed,
        "reference_date": reference_date.isoformat(),
        "clinic_asr_hint": ASR_HINT,
        "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        "suite_version": suite["version"], "caller_speaker": 20, "agent_speaker": 0,
        "transport": "production VoiceSession in-process client; real-time PCM; no browser or physical devices",
        "scoring": "SQLite state, rendered spoken success claims, known-script WER; no LLM judge",
        "scenarios": []}
    cache = {}
    for completed_index, (index, scenario) in enumerate(scenarios):
        name = scenario["id"]
        if progress:
            progress(completed_index, len(scenarios), name)
        steps = scenario["steps"]
        audio_steps = []
        references = []
        for step in steps:
            if isinstance(step, dict) and step.get("hangup"):
                audio_steps.append(None)
                continue
            if isinstance(step, dict) and "dtmf" in step:
                audio_steps.append(tone(step["dtmf"]))
                continue
            text = step if isinstance(step, str) else step["say"]
            references.append(text)
            key = (text, scenario.get("speed", 1.0), scenario.get("snr_db"), seed + index if scenario.get("snr_db") is not None else 0)
            if key not in cache:
                cache[key] = await caller_audio(runtime.tts, text, key[1], key[2], key[3])
            audio_steps.append(cache[key])
        store_type = FaultInjectingStore if scenario.get("fail_writes") else BookingStore
        store = store_type(directory / name / "state.sqlite3", lambda:reference_date)
        caller, _ = store.identity()
        foreign_before = None
        foreign = None
        if scenario.get("foreign_booking"):
            foreign, _ = store.identity()
            fields = grounded_fields("Monday at three PM with Doctor Lee", store.current_date())
            foreign_before = store.write(foreign, uuid.uuid4().hex, "book", {k:fields[k] for k in ("date", "time", "clinician")})
        client = SyntheticClient(runtime, BookingWorkflow(store, caller, runtime.llm), scenario.get("phone", False))
        error = None
        try:
            for number, (step, pcm) in enumerate(zip(steps, audio_steps)):
                if pcm is None:
                    break
                interrupt_next = number + 1 < len(steps) and isinstance(steps[number + 1], dict) and steps[number + 1].get("interrupt")
                interrupted = isinstance(step, dict) and step.get("interrupt", False)
                await client.feed(pcm, interrupted)
                await client.receive(until_audio=interrupt_next)
        except Exception as exc:
            error = type(exc).__name__
        finally:
            await client.session.close()
        rows = store.list(caller)
        active = [row for row in rows if row["status"] == "confirmed"]
        expected = scenario["expected"]
        task_success = error is None and len(active) == expected["active"]
        if expected.get("cancelled"):
            task_success &= sum(row["status"] == "cancelled" for row in rows) == expected["cancelled"]
        if expected.get("day"):
            desired = grounded_fields(f"{expected['day']} at {expected['time']} with Doctor {expected['clinician']}", store.current_date())
            task_success &= any(all(row[k] == desired[k] for k in ("date", "time", "clinician")) for row in active)
        foreign_unchanged = foreign is None or store.get(foreign, foreign_before["id"]) == foreign_before
        task_success &= foreign_unchanged
        unique_rows = {(row["date"], row["time"], row["clinician"]) for row in rows}
        duplicate_writes = max(0, len(rows) - len(unique_rows))
        # Align only spoken turns: DTMF is a separate input channel, never ASR WER.
        recognized = client.transcripts
        reference = " ".join(references)
        hypothesis = " ".join(t for t in recognized if t not in {"yes", "no"}) if any(isinstance(s, dict) and "dtmf" in s for s in steps) else " ".join(recognized)
        normalized_wer = wer(reference, hypothesis)
        with wave.open(str(directory / f"{name}.wav"), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"".join(client.audio))
        item = {"id": name, "task_success": bool(task_success), "error_class": error,
            "false_confirmations": client.false_claims, "success_claims": client.claims,
            "duplicate_writes": duplicate_writes, "asr_wer": normalized_wer,
            "voice_to_voice_ms": [m.get("server_voice_to_voice_ms") for m in client.metrics if m.get("asr_audio_s",0)>0],
            "barge_in": client.barge, "barge_in_success": bool(client.barge) and all(b["generation_fenced"] for b in client.barge) if any(isinstance(s, dict) and s.get("interrupt") for s in steps) else None,
            "phone": scenario.get("phone", False), "state": rows, "foreign_unchanged": foreign_unchanged,
            "expected": expected,
            "perturbations": {"speed":scenario.get("speed",1.0),"snr_db":scenario.get("snr_db"),"seed":seed+index},
            "trace": client.trace, "stage_metrics": client.metrics,
            "replay": f"{name}.wav", "synthetic_audio_only": True,
            "replay_content":"concatenated synthetic caller and agent turns; latency is recorded separately"}
        report["scenarios"].append(item)
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    results = report["scenarios"]
    report["status"] = "completed"
    report["summary"] = {
        "scenario_count": len(results), "task_success_rate": sum(r["task_success"] for r in results) / len(results),
        "false_confirmation_rate": sum(r["false_confirmations"] for r in results) / max(1, sum(r["success_claims"] for r in results)),
        "duplicate_write_rate": sum(r["duplicate_writes"] > 0 for r in results) / len(results),
        "asr_wer": float(np.mean([r["asr_wer"] for r in results])),
        "phone_asr_wer": float(np.mean([r["asr_wer"] for r in results if r["phone"]])) if any(r["phone"] for r in results) else None,
        "voice_to_voice_ms": percentile([v for r in results for v in r["voice_to_voice_ms"]]),
        "barge_in_success_rate": float(np.mean([r["barge_in_success"] for r in results if r["barge_in_success"] is not None])) if any(r["barge_in_success"] is not None for r in results) else None,
        "barge_in_stop_ms": percentile([b["stop_dispatch_ms"] for r in results for b in r["barge_in"]])}
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if progress:
        progress(len(scenarios), len(scenarios), "completed")
    return report
