"""Exercise real production adapters separately, with admission before each load.

This is adapter validation, never a combined voice-to-voice benchmark.
"""
import asyncio
import datetime as dt
import json
import sys
import time
import wave
from contextlib import aclosing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_live import metadata, source_hash
from pipeline.admission import InsufficientMemory
from pipeline.native import LlamaLLM, WhisperASR
from pipeline.tts import PiperTTS
from server.runtime import Runtime


async def tts_check():
    tts = PiperTTS()
    try:
        await tts.start()
        original = tts.proc
        # Cancel while the real worker is servicing a request, before accepting
        # audio. Closing the generator kills ONNX work, not merely a Python task.
        stream = tts.stream("This is a native cancellation check, " + "please listen carefully " * 30 + ".")
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.01)
        first_audio_already_ready = task.done()
        t = time.perf_counter()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await stream.aclose()
        cancel_ms = (time.perf_counter() - t) * 1000
        assert original.returncode is not None and tts.proc is None
        await tts.start()
        reloaded = tts.proc.pid != original.pid
        t = time.perf_counter()
        first = None
        samples = 0
        async with aclosing(tts.stream("Hello. The local voice pipeline is ready.")) as audio:
            async for rate, pcm in audio:
                if first is None:
                    first = (time.perf_counter() - t) * 1000
                samples += len(pcm) // 2
        assert samples > 0 and reloaded
        return {"status": "passed", "cancel_process_exit_ms": cancel_ms,
            "first_audio_ready_before_cancel": first_audio_already_ready,
            "worker_reloaded_after_cancel": reloaded, "first_chunk_after_reload_ms": first,
            "samples_after_reload": samples, "output_rate": rate}
    finally:
        await tts.close()


async def asr_check():
    runtime = Runtime()
    try:
        url = await runtime.launch("third_party/whisper.cpp/build/bin/whisper-server",
            "models/asr/ggml-base.en.bin", 322, [], "/")
        path = ROOT / "benchmarks/phase0/audio/caller_short.wav"
        with wave.open(str(path)) as wav:
            pcm = wav.readframes(wav.getnframes())
        t = time.perf_counter()
        text = await WhisperASR(runtime.client, url).transcribe(pcm)
        assert text.strip(), "Real ASR returned no transcript"
        from run_phase0 import wer
        reference = path.with_suffix(".txt").read_text()
        return {"status": "passed", "asr_ms": (time.perf_counter() - t) * 1000,
                "normalized_wer": wer(reference, text), "audio_s": len(pcm) / 32000}
    finally:
        await runtime.close()


async def llm_check():
    runtime = Runtime()
    try:
        url = await runtime.launch("third_party/llama.cpp/build/bin/llama-server",
            "models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf", 1313,
            ["-c", "4096", "-np", "1", "-ngl", "99", "--no-webui", "--log-disable"], "/health")
        llm = LlamaLLM(runtime.client, url)
        stream = llm.stream([{"role": "user", "content": "Count from one to ninety, slowly, with each number in words."}])
        async with aclosing(stream):
            first = await anext(stream)
            assert first
            slots = (await runtime.client.get(url + "/slots")).json()
            processing_before = any(slot["is_processing"] for slot in slots)
        t = time.perf_counter()
        async with asyncio.timeout(5):
            while True:
                slots = (await runtime.client.get(url + "/slots")).json()
                if not any(slot["is_processing"] for slot in slots):
                    break
                await asyncio.sleep(0.01)
        released_ms = (time.perf_counter() - t) * 1000
        # A following request must also work after closing the previous stream.
        text = ""
        async with aclosing(llm.stream([{"role": "user", "content": "Say hello in one short sentence."}])) as tokens:
            async for token in tokens:
                text += token
        assert text.strip()
        return {"status": "passed", "slot_processing_before_close": processing_before,
                "slot_idle_after_close_ms": released_ms, "followup_nonempty": True,
                "followup_native_timings": llm.timings}
    finally:
        await runtime.close()


async def main():
    result = {"metadata": metadata(), "source_sha256": source_hash(), "mode": "REAL",
        "scope": "Each adapter separately. No simultaneous model group or physical audio.", "checks": {}}
    for name, check in (("piper", tts_check), ("whisper", asr_check), ("llama", llm_check)):
        try:
            result["checks"][name] = await check()
        except InsufficientMemory as exc:
            result["checks"][name] = {"status": "blocked_memory_admission", "reason": str(exc)}
        except Exception as exc:
            result["checks"][name] = {"status": "failed", "error_class": type(exc).__name__}
        print(name, json.dumps(result["checks"][name]), flush=True)
    directory = ROOT / "benchmarks/phase1/results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"adapters-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(path.relative_to(ROOT))
    if any(c["status"] == "failed" for c in result["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
