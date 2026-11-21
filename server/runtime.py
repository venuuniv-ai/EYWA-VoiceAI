"""Own native processes, check memory before every load, and clean up on failure."""
import asyncio
import json
import os
import resource
import socket
import subprocess
from pathlib import Path

import httpx

from pipeline.admission import MB, check_admission
from pipeline.native import LlamaLLM, WhisperASR
from pipeline.tts import PiperTTS
from pipeline.vad import SileroVAD
from pipeline.profiling import measured_command, signal_owned
from server.tracing import LocalTracing

ROOT = Path(__file__).resolve().parents[1]
LLM_PROFILES = {
    "balanced": {"model": "models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf", "memory_mb": 1313},
    # Full evaluation reached 640.875 MiB; this admission budget includes margin.
    "compact": {"model": "models/llm/qwen2.5-0.5b-instruct-q4_k_m.gguf", "memory_mb": 680},
}


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Runtime:
    mode = "REAL — whisper.cpp / llama.cpp / Silero / Piper; offline inference"

    def __init__(self, profile: str | None = None):
        self.profile = profile or os.environ.get("EYWA_MODEL_PROFILE", "balanced")
        if self.profile not in LLM_PROFILES:
            raise ValueError("EYWA_MODEL_PROFILE must be balanced or compact")
        self.llm_settings = LLM_PROFILES[self.profile]
        self.mode = f"REAL — {self.profile}: {Path(self.llm_settings['model']).stem}; whisper.cpp / llama.cpp / Silero / Piper"
        self.processes: list[subprocess.Popen] = []
        self.profiled: dict[int, bool] = {}
        self.client = httpx.AsyncClient(timeout=60, trust_env=False)
        self.tts = PiperTTS()
        self.tracing = LocalTracing(ROOT / "runtime/traces")
        self.vad = None

    async def launch(self, binary: str, model: str, memory_mb: int, args: list[str], health: str):
        check_admission(model, memory_mb * MB)
        port = unused_port()
        command, profiled = measured_command([str(ROOT / binary), "-m", str(ROOT / model), "-t", "4",
            "--host", "127.0.0.1", "--port", str(port), *args], Path(binary).name)
        proc = subprocess.Popen(command,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
        self.processes.append(proc)
        self.profiled[proc.pid] = profiled
        url = f"http://127.0.0.1:{port}"
        async with asyncio.timeout(120):
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f"Native server exited with code {proc.returncode}: {binary}")
                try:
                    response = await self.client.get(url + health, timeout=1)
                    if response.status_code == 200:
                        return url
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)

    async def start(self):
        # Check the next model against *current* available RAM. Summing all
        # isolated RSS peaks up front overcounts reclaimable mmap/shared pages.
        # Each load still keeps the same 512 MB reserve; failures unwind lifespan.
        llm_url = await self.launch("third_party/llama.cpp/build/bin/llama-server",
            self.llm_settings["model"], self.llm_settings["memory_mb"],
            ["-c", "4096", "-np", "1", "-ngl", "99", "--no-webui", "--log-disable"], "/health")
        asr_url = await self.launch("third_party/whisper.cpp/build/bin/whisper-server",
            "models/asr/ggml-base.en.bin", 322, [], "/")
        check_admission("Silero VAD", 76 * MB)
        self.vad = SileroVAD()
        await self.tts.start()
        self.llm, self.asr = LlamaLLM(self.client, llm_url), WhisperASR(self.client, asr_url)

    async def close(self):
        await self.tts.close()
        await self.client.aclose()
        for proc in reversed(self.processes):
            if proc.poll() is None:
                signal_owned(proc.pid, self.profiled[proc.pid])
        for proc in reversed(self.processes):
            try:
                await asyncio.to_thread(proc.wait, 5)
            except subprocess.TimeoutExpired:
                signal_owned(proc.pid, self.profiled[proc.pid], force=True)
                await asyncio.to_thread(proc.wait)
        self.tracing.close()
        if directory := os.environ.get("EYWA_PROFILE_DIR"):
            # wait4/time on a parent can include descendant usage. RUSAGE_SELF
            # records the gateway's own peak, so the group sum won't count LLM twice.
            (Path(directory) / "gateway-self.json").write_text(json.dumps({
                "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}) + "\n")
