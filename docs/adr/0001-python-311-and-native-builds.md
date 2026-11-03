# 0001: Python 3.11 venv and native Metal builds at pinned tags

Date: 2026-11-03. Status: accepted.

## Context
The machine's default `python3` is 3.14.8. That is very new, and binary wheels for audio/ML packages (onnxruntime in particular) often arrive late for new Python versions. Homebrew `python@3.11` (3.11.17) is already installed. whisper.cpp and llama.cpp must run natively to use the Metal GPU (no Docker for inference).

## Decision
- The project venv (`.venv`) uses Homebrew Python 3.11. Dependencies are pinned in `requirements.txt`.
- whisper.cpp `v1.9.5` and llama.cpp `b12066` are shallow-cloned into `third_party/` and built with `-DGGML_METAL=ON` by `scripts/setup_native.sh`. Only the targets EYWA uses are built: `whisper-cli`, `whisper-bench`, `whisper-server`, `llama-cli`, `llama-bench` and `llama-server`.

## Alternatives
- Python 3.14: rejected for now because of the wheel-availability risk. Revisit once every dependency publishes wheels for it.
- Homebrew `llama.cpp` / `whisper-cpp` bottles: they're simpler, but their versions float with `brew upgrade`, which breaks reproducible benchmarks.
- `llama-cpp-python` / `pywhispercpp` bindings: these compile their own copy of ggml, which costs more disk space and adds a second build to keep in sync.

## Tradeoffs
Building from source takes about 2 minutes and about 0.5 GB of disk. Upgrading means bumping the tags in one script and re-running the benchmarks.
