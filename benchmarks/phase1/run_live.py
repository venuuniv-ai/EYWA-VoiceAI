"""Real-model WebSocket benchmark. Admission refusals are saved, never bypassed.

Feeds existing synthetic-caller WAVs at capture speed through Silero endpointing,
ASR, streamed LLM and Piper. This measures first audio *received*, not acoustic
speaker onset. It does not claim human-mic or browser barge-in validation.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx
import psutil
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks/phase0"))
from run_phase0 import metadata, pct
from pipeline.admission import MB, decide
from pipeline.vad import SileroVAD
from server.runtime import LLM_PROFILES
from turn.endpoint import Endpointer
from pipeline.profiling import signal_owned
from pipeline.tts import PIPER_MEMORY_MB


def source_hash():
    h = hashlib.sha256()
    for directory in ("pipeline", "server", "turn", "connect", "eval", "web/src", "web/public",
                      "tests", "scripts", "benchmarks/phase1"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.suffix in (".py", ".ts", ".tsx", ".js", ".css"):
                h.update(str(path.relative_to(ROOT)).encode())
                h.update(path.read_bytes())
    for name in ("requirements.txt", "pyproject.toml", "web/package-lock.json", "Makefile"):
        h.update((ROOT / name).read_bytes())
    return h.hexdigest()


def fixture(name):
    path = ROOT / f"benchmarks/phase0/audio/{name}.wav"
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getsampwidth() == 2 and w.getnchannels() == 1
        pcm = w.readframes(w.getnframes())
    # Last speech frame, according to the same real VAD: explicit metric boundary.
    vad = SileroVAD()
    endpoint = Endpointer()
    padded = pcm + b"\0" * (-len(pcm) % 1024) + b"\0" * 1024 * 20
    frames = [padded[i:i + 1024] for i in range(0, len(padded), 1024)]
    speech, completed = [], []
    for i, frame in enumerate(frames):
        probability = vad.probability(frame)
        if probability >= 0.5:
            speech.append(i)
        event = endpoint.feed(frame, probability)
        if event.pcm:
            completed.append(event)
    if not speech:
        raise RuntimeError("No speech detected in benchmark fixture")
    if len(completed) != 1 or completed[0].speech_end_ms != (speech[-1] + 1) * 32:
        raise RuntimeError("Latency fixture must contain exactly one complete speech turn")
    return frames, speech[-1], hashlib.sha256(path.read_bytes()).hexdigest()


async def one_turn(url, name, prepared):
    frames, last_speech, checksum = prepared
    async with connect(url, max_size=1024 * 1024) as ws:
        ready = json.loads(await ws.recv())
        assert ready["mode"].startswith("REAL"), "Benchmark refuses mock inference"
        last_speech_at = first_audio_at = None
        metrics = None

        async def feed():
            nonlocal last_speech_at
            next_frame = time.perf_counter()
            for i, frame in enumerate(frames):
                await asyncio.sleep(max(0, next_frame - time.perf_counter()))
                await ws.send(frame)
                if i == last_speech:
                    last_speech_at = time.perf_counter()
                next_frame += 0.032

        async def receive():
            nonlocal first_audio_at, metrics
            async for raw in ws:
                event = json.loads(raw)
                if event["type"] == "audio" and first_audio_at is None:
                    first_audio_at = time.perf_counter()
                if event["type"] == "clause_end":
                    # Transport-only benchmark consumes audio immediately. This
                    # is not a claim that physical playback has completed.
                    await ws.send(json.dumps({"type": "played", "turn_id": event["turn_id"],
                                              "clause_id": event["clause_id"]}))
                if event["type"] == "response_done":
                    metrics = event["metrics"]
                    return
                if event["type"] == "error":
                    raise RuntimeError(event["message"])
        async with asyncio.timeout(90):
            async with asyncio.TaskGroup() as group:
                group.create_task(feed())
                group.create_task(receive())
        if first_audio_at is None or last_speech_at is None or metrics is None:
            raise RuntimeError("No complete real audio turn")
        return {"fixture": name, "fixture_sha256": checksum, **metrics,
            "transport_voice_to_voice_ms": (first_audio_at - last_speech_at) * 1000}


async def run(rounds, browser_checks=False, profile="balanced"):
    llm_settings = LLM_PROFILES[profile]
    record = {"metadata": metadata(), "source_sha256": source_hash(), "mode": "REAL",
        "measurement": "PCM paced at 32 ms/frame; last Silero speech frame sent to first audio received",
        "settings": {"rounds": rounds, "warmup_turns": 1, "silence_ms": 480,
            "onset_ms": 96, "min_speech_ms": 160, "max_turn_ms": 15000,
            "max_tokens": 96, "cache_prompt": True, "playback": "immediate transport consumer"},
        "runs": []}
    record["settings"]["llm_profile"] = profile
    record["settings"]["active_llm_file"] = llm_settings["model"]
    admission = decide("first model: Qwen", llm_settings["memory_mb"] * MB)
    record["admission"] = {"admitted": admission.admitted, "available_bytes": admission.available_bytes,
        "required_bytes": admission.required_bytes, "headroom_bytes": admission.headroom_bytes,
        "reason": admission.reason}
    record["admission"]["policy"] = "sequential per-model checks, 512 MB reserve before every load"
    record["admission"]["sum_admission_budgets_bytes"] = (llm_settings["memory_mb"] + 322 + 76 + PIPER_MEMORY_MB) * MB
    if not admission.admitted:
        record["status"] = "blocked_memory_admission"
        return record

    names = ("caller_short", "caller_medium", "caller_long")
    prepared = {name: fixture(name) for name in names}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = ROOT / "benchmarks/phase1/logs" / stamp
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / "server.log").open("w")
    server = subprocess.Popen(["/usr/bin/time", "-l", "-o", str(log_dir / "gateway.txt"),
        sys.executable, "-m", "uvicorn", "server.app:app",
        "--host", "127.0.0.1", "--port", str(port), "--no-access-log",
        "--ws-max-size", "4096", "--ws-max-queue", "16"], cwd=ROOT, stdout=log, stderr=log,
        env={**os.environ, "EYWA_PROFILE_DIR": str(log_dir), "EYWA_MODEL_PROFILE": profile})
    rss_samples = []
    monitoring = True

    async def sample_memory():
        while monitoring:
            try:
                parent = psutil.Process(server.pid)
                processes = [parent, *parent.children(recursive=True)]
                values = []
                for p in processes:
                    try:
                        values.append({"pid": p.pid, "name": p.name(), "rss_bytes": p.memory_info().rss})
                    except psutil.NoSuchProcess:
                        pass
                rss_samples.append({"at_monotonic_s": time.perf_counter(), "processes": values,
                                    "available_bytes": psutil.virtual_memory().available})
            except psutil.NoSuchProcess:
                return
            await asyncio.sleep(0.1)
    monitor = asyncio.create_task(sample_memory())
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            async with asyncio.timeout(150):
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Real server startup failed; see benchmarks/phase1/logs/server.log")
                    try:
                        response = await client.get(f"http://127.0.0.1:{port}/health", timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
        url = f"ws://127.0.0.1:{port}/ws"
        record["warmup"] = await one_turn(url, names[0], prepared[names[0]])
        for round_id in range(rounds):
            for name in names:
                result = await one_turn(url, name, prepared[name])
                result["round"] = round_id
                record["runs"].append(result)
                print(f"REAL {name}: first audio received {result['transport_voice_to_voice_ms']:.0f} ms", flush=True)
        record["status"] = "completed_transport_benchmark"
        record["summary"] = {key: pct([r.get(key) for r in record["runs"]]) for key in (
            "transport_voice_to_voice_ms", "server_voice_to_voice_ms", "vad_endpoint_ms", "asr_ms",
            "asr_rtf", "llm_ttft_ms", "llm_tokens_per_second", "tts_first_chunk_ms")}
        if browser_checks:
            from benchmarks.phase1.browser_live import probe
            record["browser"] = await probe(f"http://127.0.0.1:{port}")
    except Exception as exc:
        record["status"] = "failed"
        record["error_class"] = type(exc).__name__
        if hasattr(exc, "diagnostic"):
            record["browser"] = exc.diagnostic
        print(f"Benchmark failed ({type(exc).__name__}); inspect local server log.", file=sys.stderr)
    finally:
        monitoring = False
        await monitor
        signal_owned(server.pid, True)
        try:
            await asyncio.to_thread(server.wait, 10)
        except subprocess.TimeoutExpired:
            # Stop all owned children if the server cannot shut down cleanly.
            for child in psutil.Process(server.pid).children(recursive=True):
                child.kill()
            server.kill()
            await asyncio.to_thread(server.wait)
        log.close()
        peaks = {}
        for file in log_dir.glob("*.txt"):
            raw = file.read_text()
            match = re.search(r"(\d+)\s+maximum resident set size", raw)
            if match:
                name = "piper" if file.stem.startswith("piper-") else file.stem
                peaks[name] = max(peaks.get(name, 0), int(match[1]))
        own_peak = log_dir / "gateway-self.json"
        if own_peak.exists():
            peaks["gateway"] = json.loads(own_peak.read_text())["max_rss_bytes"]
        record["memory"] = {"method": "100 ms psutil sampling; sampled peak, not exact peak RSS",
            "sampled_group_peak_rss_bytes": max((sum(p["rss_bytes"] for p in s["processes"])
                                                 for s in rss_samples), default=0),
            "samples": rss_samples, "isolated_process_max_rss_bytes": peaks,
            "sum_process_max_rss_bytes": sum(peaks.values()) if len(peaks) == 4 else None,
            "peak_method": "/usr/bin/time -l per owned process; sum is a conservative upper bound"}
        record["memory"]["raw_time_output"] = {file.stem: file.read_text() for file in log_dir.glob("*.txt")}
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=7, help="3 clips per round; warmup excluded")
    parser.add_argument("--browser", action="store_true", help="Also exercise real inference in Chromium with fixture input")
    parser.add_argument("--profile", choices=LLM_PROFILES, default="balanced")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    record = asyncio.run(run(args.rounds, args.browser, args.profile))
    directory = ROOT / "benchmarks/phase1/results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"live-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(record["status"], path.relative_to(ROOT))
    if "summary" in record:
        print(json.dumps(record["summary"], indent=2))
    raise SystemExit(0 if record["status"].startswith("completed") else 1)


if __name__ == "__main__":
    main()
