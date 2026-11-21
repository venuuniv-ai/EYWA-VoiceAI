"""First isolated compact-model measurement, with live 512 MB reserve monitoring.

An unmeasured model has no claimed RSS requirement. Before this profiling-only
run, require room for its on-disk weights and the reserve; kill it if available
memory crosses the reserve. Production only uses the resulting measured peak.
"""
import asyncio
import datetime as dt
import json
import re
import sys
import time
from contextlib import aclosing
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks/phase0"))

from run_phase0 import metadata, pct
from pipeline.admission import MB, check_admission
from pipeline.native import LlamaLLM
from server.runtime import Runtime
from pipeline.profiling import signal_owned


async def main():
    model = ROOT / "models/llm/qwen2.5-0.5b-instruct-q4_k_m.gguf"
    check_admission("unmeasured compact profiling weights", model.stat().st_size)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = ROOT / "benchmarks/phase1/logs" / f"compact-{stamp}"
    directory.mkdir(parents=True, exist_ok=True)
    import os
    os.environ["EYWA_PROFILE_DIR"] = str(directory)
    runtime = Runtime()
    record = {"metadata": metadata(), "mode": "REAL isolated compact LLM",
        "settings": {"ctx": 4096, "threads": 4, "gpu_layers": 99, "parallel": 1, "max_tokens": 96},
        "initial_profiling_admission": "weights-size lower bound, with continuous reserve guard; not a measured RSS claim",
        "samples": [], "runs": []}
    stopped = asyncio.Event()
    violated = False

    async def monitor():
        nonlocal violated
        while not stopped.is_set():
            available = psutil.virtual_memory().available
            record["samples"].append({"t": time.perf_counter(), "available_bytes": available})
            if available < 512 * MB:
                violated = True
                for process in runtime.processes:
                    signal_owned(process.pid, runtime.profiled[process.pid])
                return
            await asyncio.sleep(0.02)
    monitoring = asyncio.create_task(monitor())
    try:
        url = await runtime.launch("third_party/llama.cpp/build/bin/llama-server",
            str(model.relative_to(ROOT)), (model.stat().st_size + MB - 1) // MB,
            ["-c", "4096", "-np", "1", "-ngl", "99", "--no-webui", "--log-disable"], "/health")
        llm = LlamaLLM(runtime.client, url)
        for i in range(8):
            t = time.perf_counter()
            first = None
            async with aclosing(llm.stream([{"role": "system", "content": "Reply in two short spoken sentences."},
                {"role": "user", "content": "How can I make a voice call with a local assistant?"}])) as stream:
                async for token in stream:
                    if first is None:
                        first = (time.perf_counter() - t) * 1000
            record["runs"].append({"warmup": i == 0, "ttft_ms": first, "native_timings": llm.timings})
        record["status"] = "passed" if not violated else "reserve_violated"
    except Exception as exc:
        record["status"] = "failed"
        record["error_class"] = type(exc).__name__
    finally:
        stopped.set()
        await monitoring
        await runtime.close()
    raw = (directory / "llama-server.txt").read_text()
    match = re.search(r"(\d+)\s+maximum resident set size", raw)
    record["max_rss_bytes"] = int(match[1]) if match else None
    record["reserve_violated"] = violated
    record["summary"] = {"ttft_ms": pct([r["ttft_ms"] for r in record["runs"] if not r["warmup"]])}
    path = ROOT / "benchmarks/phase1/results" / f"compact-{stamp}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(path.relative_to(ROOT), record["status"], "peak RSS MB", record["max_rss_bytes"] / MB)
    print(json.dumps(record["summary"]))
    if record["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
