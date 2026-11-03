# 0002: Run ASR and LLM as resident native HTTP servers

Date: 2026-11-03. Status: accepted for Phase 1 (re-evaluate if IPC overhead shows up in the measurements).

## Context
ASR and LLM have to stay loaded, because reloading per turn costs 0.4–1.5 s (see `benchmarks/phase0/summary.md`). The Python backend needs streaming LLM tokens and cancellation for barge-in.

## Decision
The FastAPI backend supervises two child processes, `llama-server` and `whisper-server`, bound to `127.0.0.1`. LLM tokens stream over SSE. Cancelling a generation means closing the HTTP stream, which llama-server detects and aborts. Silero VAD and Piper TTS run in-process in Python, using onnxruntime on the CPU.

## Alternatives
- Python bindings in-process: no IPC, but a native crash takes down the whole backend, cancellation still needs threads, and the bindings build a second copy of ggml.
- Spawning `whisper-cli` for each utterance: it reloads the model every time (measured ready time: 358 ms for base.en), and the first run on Metal can take seconds.

## Tradeoffs
- Each request pays localhost HTTP overhead. That overhead is included in the measured ASR latency (p50 176 ms for base.en, upload included; benchmark run at commit a4e7251).
- Process isolation also makes per-component memory measurement simple (`/usr/bin/time -l`).
- Two extra processes have to be supervised: health checks, restart, and shutdown.
- Cancelling via a closed stream still has to be checked with a test in Phase 1.
