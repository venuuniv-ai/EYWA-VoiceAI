"""Phase 0 feasibility benchmark: each model alone, then all together.

Usage:  .venv/bin/python benchmarks/phase0/run_phase0.py [--quick]

Memory is measured with macOS `/usr/bin/time -l` around each process:
  * "maximum resident set size" is the budget metric. It includes the
    memory-mapped model file pages (llama.cpp mmaps the GGUF);
  * "peak memory footprint" (phys_footprint: dirty/anonymous memory incl.
    Metal buffers) is recorded alongside it, but it omits clean mmap'd pages.
Raw results go to benchmarks/phase0/results/phase0-<UTC timestamp>.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import psutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from download_models import FILES  # noqa: E402

PY = str(ROOT / ".venv/bin/python")
WBIN = ROOT / "third_party/whisper.cpp/build/bin"
LBIN = ROOT / "third_party/llama.cpp/build/bin"
QWEN = ROOT / "models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf"
WHISPER = {"base.en": ROOT / "models/asr/ggml-base.en.bin", "small.en": ROOT / "models/asr/ggml-small.en.bin"}
AUDIO = HERE / "audio"
LLM_PORT, ASR_PORT = 18080, 18081
LLM_CTX = 4096
THREADS = 4
CALLER_SPEAKER = 20  # libritts_r speaker index; agent uses 0
SYSTEM_PROMPT = ("You are the phone receptionist for Maplewood Family Clinic, a fictional clinic. "
                 "Answer in one or two short spoken sentences.")
LLM_PROMPTS = [
    "I'd like to book an appointment.",
    "Can I see Doctor Lee next Tuesday at three?",
    "What are your opening hours on Saturday?",
    "I need to cancel my appointment for tomorrow.",
    "Actually, make that Thursday morning instead.",
]


# ------------------------------------------------------------------ utils
def pct(xs: list[float]) -> dict:
    a = np.asarray([x for x in xs if x is not None], dtype=float)
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)),
            "min": float(a.min()), "max": float(a.max()), "mean": float(a.mean())}


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"unavailable ({e.__class__.__name__})"


def metadata() -> dict:
    import onnxruntime
    from importlib.metadata import version

    dirty = sh(["git", "-C", str(ROOT), "status", "--porcelain"])
    return {
        "date_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "git_commit": sh(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
        "git_dirty": bool(dirty) and not dirty.startswith("unavailable"),
        "hardware": {"chip": sh(["sysctl", "-n", "machdep.cpu.brand_string"]),
                     "ram_bytes": psutil.virtual_memory().total, "cpu_count": psutil.cpu_count()},
        "os": f"macOS {platform.mac_ver()[0]}",
        "python": platform.python_version(),
        "packages": {"onnxruntime": onnxruntime.__version__, "piper-tts": version("piper-tts"),
                     "numpy": np.__version__},
        "native": {d: sh(["git", "-C", str(ROOT / "third_party" / d), "describe", "--tags", "--always"])
                   for d in ("whisper.cpp", "llama.cpp")},
        "models": [{"dest": f.dest, "sha256": f.sha256} for f in FILES],
        "settings": {"llm_ctx": LLM_CTX, "llm_parallel_slots": 1, "llm_gpu_layers": "all (99)",
                     "threads": THREADS, "temperature": 0, "asr_backend": "whisper-server, Metal",
                     "vad_frame_samples": 512, "tts_agent_speaker": 0, "tts_caller_speaker": CALLER_SPEAKER},
        "system_memory_at_start": {"available_bytes": psutil.virtual_memory().available},
    }


class Measured:
    """Run a command under /usr/bin/time -l; on stop(), parse its peak memory."""

    def __init__(self, name: str, cmd: list[str], log_dir: Path):
        self.name = name
        self.log = log_dir / f"{name}.log"
        self.fh = self.log.open("w")
        self.proc = subprocess.Popen(["/usr/bin/time", "-l", *cmd], stdout=self.fh, stderr=subprocess.STDOUT)
        self.t0 = time.perf_counter()

    def child(self) -> psutil.Process | None:
        try:
            kids = psutil.Process(self.proc.pid).children()
            return kids[0] if kids else None
        except psutil.NoSuchProcess:
            return None

    def wait(self, timeout: float = 600) -> None:
        self.proc.wait(timeout=timeout)

    def stop(self) -> None:
        c = self.child()
        if c is not None:
            c.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            if c is not None:
                c.kill()
            self.proc.wait()

    def memory(self) -> dict:
        self.fh.close()
        text = self.log.read_text(errors="replace")
        rss = re.findall(r"(\d+)\s+maximum resident set size", text)
        fp = re.findall(r"(\d+)\s+peak memory footprint", text)
        return {"peak_footprint_bytes": int(fp[-1]) if fp else None,
                "max_rss_bytes": int(rss[-1]) if rss else None}


def wait_http(url: str, timeout: float = 180) -> float:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return (time.perf_counter() - t0) * 1000
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.05)
    raise TimeoutError(url)


def wav_seconds(path: Path) -> float:
    import wave

    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


_NUM = dict(zip(map(str, range(13)), "zero one two three four five six seven eight nine ten eleven twelve".split()))
_ABBR = {"dr": "doctor", "mr": "mister", "mrs": "missus"}


def wer(ref: str, hyp: str, normalize: bool = True) -> float:
    """Word error rate. normalize=True maps digits 0-12 and common abbreviations to words
    (so "Dr. Lee at 3" == "Doctor Lee at three"); normalize=False only lowercases/strips punctuation."""
    def norm(s: str) -> list[str]:
        words = re.sub(r"[^a-z0-9' ]", " ", s.lower().replace("-", " ")).split()
        return [_ABBR.get(w, _NUM.get(w, w)) for w in words] if normalize else words

    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


# ------------------------------------------------------------------ clients
def asr_request(port: int, wav: Path) -> dict:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in {"response_format": "json", "temperature": "0.0"}.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{wav.name}"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + wav.read_bytes() + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/inference", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read())
    return {"latency_ms": (time.perf_counter() - t0) * 1000, "text": res.get("text", "").strip()}


def llm_stream(port: int, user: str, max_tokens: int = 64) -> dict:
    payload = {"messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
               "stream": True, "max_tokens": max_tokens, "temperature": 0, "seed": 42, "cache_prompt": False}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    ttft, text, timings = None, [], None
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            ev = json.loads(line[6:])
            if ev.get("timings"):
                timings = ev["timings"]
            for ch in ev.get("choices", []):
                piece = (ch.get("delta") or {}).get("content")
                if piece:
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000
                    text.append(piece)
    return {"ttft_ms": ttft, "total_ms": (time.perf_counter() - t0) * 1000, "text": "".join(text),
            "timings": timings}


def start_llm(log_dir: Path, name: str = "llm") -> tuple[Measured, float]:
    m = Measured(name, [str(LBIN / "llama-server"), "-m", str(QWEN), "-c", str(LLM_CTX), "-np", "1",
                        "-ngl", "99", "-t", str(THREADS), "--host", "127.0.0.1", "--port", str(LLM_PORT)], log_dir)
    return m, wait_http(f"http://127.0.0.1:{LLM_PORT}/health")


def start_asr(model: str, log_dir: Path, name: str) -> tuple[Measured, float]:
    m = Measured(name, [str(WBIN / "whisper-server"), "-m", str(WHISPER[model]), "-t", str(THREADS),
                        "--host", "127.0.0.1", "--port", str(ASR_PORT)], log_dir)
    return m, wait_http(f"http://127.0.0.1:{ASR_PORT}/")


def run_worker(name: str, args: list[str], log_dir: Path) -> tuple[dict, dict]:
    m = Measured(name, [PY, str(HERE / "workers.py"), *args], log_dir)
    m.wait()
    mem = m.memory()
    lines = [ln for ln in m.log.read_text().splitlines() if ln.startswith("{")]
    if m.proc.returncode != 0 or not lines:
        raise RuntimeError(f"worker {name} failed; see {m.log}")
    return json.loads(lines[-1]), mem


class MemSampler(threading.Thread):
    """Sample system-wide available memory while a combined run is in progress."""

    def __init__(self):
        super().__init__(daemon=True)
        self.min_avail = psutil.virtual_memory().available
        self.stop_evt = threading.Event()
        self.swap_start = psutil.swap_memory().used

    def run(self):
        while not self.stop_evt.is_set():
            self.min_avail = min(self.min_avail, psutil.virtual_memory().available)
            time.sleep(0.2)

    def result(self) -> dict:
        self.stop_evt.set()
        self.join()
        return {"min_available_bytes": self.min_avail,
                "swap_used_delta_bytes": psutil.swap_memory().used - self.swap_start}


# ------------------------------------------------------------------ benchmarks
def clips() -> list[tuple[Path, str]]:
    out = [(ROOT / "third_party/whisper.cpp/samples/jfk.wav",
            "And so my fellow Americans, ask not what your country can do for you, "
            "ask what you can do for your country.")]
    for name in ("caller_short", "caller_medium", "caller_long"):
        out.append((AUDIO / f"{name}.wav", (AUDIO / f"{name}.txt").read_text().strip()))
    return out


def bench_asr(model: str, reps: int, log_dir: Path) -> dict:
    m, load_ms = start_asr(model, log_dir, f"asr_{model}")
    try:
        warm = asr_request(ASR_PORT, clips()[0][0])
        runs = []
        for wav, ref in clips():
            secs = wav_seconds(wav)
            for _ in range(reps):
                r = asr_request(ASR_PORT, wav)
                runs.append({"clip": wav.name, "audio_s": secs, "latency_ms": r["latency_ms"],
                             "rtf": r["latency_ms"] / 1000 / secs, "wer": wer(ref, r["text"]), "wer_raw": wer(ref, r["text"], normalize=False), "text": r["text"]})
    finally:
        m.stop()
    by_clip = {}
    for wav, _ in clips():
        rs = [r for r in runs if r["clip"] == wav.name]
        by_clip[wav.name] = {"audio_s": rs[0]["audio_s"], "latency_ms": pct([r["latency_ms"] for r in rs]),
                             "rtf": pct([r["rtf"] for r in rs]), "wer": rs[0]["wer"], "wer_raw": rs[0]["wer_raw"], "text": rs[0]["text"]}
    return {"model": model, "ready_ms": load_ms, "warmup_ms": warm["latency_ms"], "memory": m.memory(),
            "latency_ms": pct([r["latency_ms"] for r in runs]), "rtf": pct([r["rtf"] for r in runs]),
            "by_clip": by_clip, "runs": runs}


def bench_llm(reps: int, log_dir: Path) -> dict:
    m, load_ms = start_llm(log_dir)
    try:
        warm = llm_stream(LLM_PORT, "Hello.")
        runs = [llm_stream(LLM_PORT, p) for _ in range(reps) for p in LLM_PROMPTS]
    finally:
        m.stop()
    tps = [r["timings"]["predicted_per_second"] for r in runs if r["timings"]]
    pps = [r["timings"]["prompt_per_second"] for r in runs if r["timings"]]
    bench = sh([str(LBIN / "llama-bench"), "-m", str(QWEN), "-p", "256", "-n", "64", "-r", "3",
                "-t", str(THREADS), "-o", "json"])
    try:
        bench = json.loads(bench)
    except json.JSONDecodeError:
        pass
    return {"ready_ms": load_ms, "warmup_ttft_ms": warm["ttft_ms"], "memory": m.memory(),
            "ttft_ms": pct([r["ttft_ms"] for r in runs]), "gen_tokens_per_s": pct(tps),
            "prompt_tokens_per_s": pct(pps), "sample_reply": runs[0]["text"], "runs": runs,
            "llama_bench": bench}


def bench_tts(reps: int, log_dir: Path) -> dict:
    res, mem = run_worker("tts", ["tts", str(reps)], log_dir)
    runs = res["runs"]
    return {"load_ms": res["load_ms"], "sample_rate": res["sample_rate"], "memory": mem,
            "first_chunk_ms": pct([r["first_chunk_ms"] for r in runs]), "rtf": pct([r["rtf"] for r in runs]),
            "runs": runs}


def bench_vad(log_dir: Path) -> dict:
    res, mem = run_worker("vad", ["vad", *[str(c) for c, _ in clips()]], log_dir)
    return {"load_ms": res["load_ms"], "memory": mem, "frame_ms": res["frame_ms"],
            "frame_infer_ms": pct(res["frame_infer_ms"]), "clips": res["clips"],
            "silence_max_prob": res["silence_max_prob"]}


def bench_combined(asr_model: str, rounds: int, log_dir: Path) -> dict:
    sampler = MemSampler()
    sampler.start()
    llm, _ = start_llm(log_dir, f"combined_{asr_model}_llm")
    asr, _ = start_asr(asr_model, log_dir, f"combined_{asr_model}_asr")
    try:
        res, py_mem = run_worker(f"combined_{asr_model}_py",
                                 ["combined", str(LLM_PORT), str(ASR_PORT), str(rounds),
                                  *[str(c) for c, _ in clips()]], log_dir)
    finally:
        asr.stop()
        llm.stop()
    parts = {"llm": llm.memory(), "asr": asr.memory(), "python_vad_tts": py_mem}
    # Budget metric: sum of per-process max RSS. Conservative: includes mmap'd model file pages
    # (llama.cpp mmaps the GGUF, which phys_footprint omits) and double-counts shared libraries.
    total_rss = sum(p["max_rss_bytes"] or 0 for p in parts.values())
    total_fp = sum(p["peak_footprint_bytes"] or 0 for p in parts.values())
    budget = int(2.5 * 1024**3)
    return {"asr_model": asr_model, "rounds": rounds, "memory": parts,
            "sum_max_rss_bytes": total_rss, "sum_peak_footprint_bytes": total_fp, "budget_bytes": budget,
            "fits_budget": total_rss < budget, "system": sampler.result(), "log": res["log"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="fewer repetitions (smoke test)")
    args = ap.parse_args()
    reps = 2 if args.quick else 10
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = HERE / "logs" / stamp
    log_dir.mkdir(parents=True, exist_ok=True)

    if not (AUDIO / "caller_long.wav").exists():
        run_worker("make_clips", ["make_clips", str(AUDIO), str(CALLER_SPEAKER)], log_dir)

    out: dict = {"meta": metadata(), "quick": args.quick}
    steps = [("vad", lambda: bench_vad(log_dir)),
             ("tts", lambda: bench_tts(max(1, reps // 2), log_dir)),
             ("asr_base.en", lambda: bench_asr("base.en", reps, log_dir)),
             ("asr_small.en", lambda: bench_asr("small.en", reps, log_dir)),
             ("llm", lambda: bench_llm(max(1, reps // 2), log_dir)),
             ("combined_base.en", lambda: bench_combined("base.en", 2 if args.quick else 3, log_dir)),
             ("combined_small.en", lambda: bench_combined("small.en", 2 if args.quick else 3, log_dir))]
    for name, fn in steps:
        print(f"== {name}", flush=True)
        out[name] = fn()
        time.sleep(2)  # let memory settle between steps

    res_dir = HERE / "results"
    res_dir.mkdir(exist_ok=True)
    path = res_dir / f"phase0-{stamp}{'-quick' if args.quick else ''}.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
