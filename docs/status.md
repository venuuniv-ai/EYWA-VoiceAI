# EYWA status

## Phase 0: Environment and feasibility (2026-11-03)

All numbers below come from `benchmarks/phase0/summary.md`, which is generated from
`benchmarks/phase0/results/phase0-20261103T091808Z.json`.

### Verified
- **Environment**: Apple M2, 8 GB, macOS 26.6.2, Homebrew 7.0.8, Node v26.10, git 2.56, FFmpeg 9.0.2 and CMake 4.4.5 (both installed in this phase), project venv on Python 3.11.17 (ADR 0001).
- **Native builds**: whisper.cpp v1.9.5 and llama.cpp b12066 built with Metal (`scripts/setup_native.sh`). The GPU is detected as `MTL0 (Apple M2)`.
- **Models**: downloaded by `scripts/download_models.py` from pinned commits/tags, and every file passed SHA-256 verification.
- **Each model measured alone** (max RSS; p50):
  - Silero VAD: 76 MB; 0.10 ms per 32 ms frame
  - Piper libritts_r: 232 MB; first chunk 35 ms; RTF 0.028
  - whisper base.en: 327 MB; 161 ms per request
  - whisper small.en: 721 MB; 432 ms per request
  - Qwen2.5-1.5B Q4_K_M (ctx 4096): 1,299 MB; TTFT 97 ms; 65.7 tok/s
- **All models together**, running a real VAD → ASR → LLM → TTS sequence: the sum of max RSS was **1,846 MB with base.en** and **2,228 MB with small.en**, both under the 2.5 GB target, with no swap growth.
- **Admission check**: `pipeline/admission.py` refuses to load a model when it would leave less than 512 MB available, and gives the reason.
- **Tests**: `make test` gives 13 passed. They cover the admission check, checksum-verified downloads (including rejecting a tampered file), WER, and the Silero wrapper on real audio.

### Decisions
- **Default ASR: `base.en`.** Both models had a normalized WER of 0 on the four clean clips, but base.en is about 2.7× faster (161 vs 432 ms p50) and uses about 400 MB less. Re-test small.en on phone-quality μ-law audio in Phase 2.
- ASR and LLM run as resident native servers (ADR 0002). The TTS voice and its license are covered in ADR 0003.
- Admission thresholds to use in Phase 1, from measured max RSS: LLM 1,309 MB; ASR base.en 327 MB; TTS+VAD process 250 MB.

### Limitations and open items
- **Benchmark not tied to a commit**: the repo had no commits when the benchmark ran, so `git_commit` is "unavailable". Re-run `make bench-phase0` after the first commit.
- **Less free memory than the spec assumed**: only 2,418 MB was available at the start of the run, below the 3–4 GB the spec assumed. The lowest system available memory during the combined run was 926–1,138 MB. EYWA fits, but closing apps changes the result.
- **The memory sum is an upper bound**: it counts the mmap'd GGUF pages and counts shared libraries once per process. Peak footprint, which leaves out clean mmap'd pages, was 704 / 1,163 MB.
- **The WER test is easy**: three of the four ASR clips are Piper-synthesized speech and the fourth is the whisper.cpp JFK sample. These are clean audio, not real callers or phone codec, so WER=0 here says little about real-world accuracy.
- **LLM TTFT used a short prompt** (about 60 tokens). Prompt processing runs at about 720 tok/s, so a 1,000-token system prompt with tool schemas would add roughly 1.4 s of prefill per uncached turn. Prompt caching in llama-server needs to be measured in Phase 1.
- **Cold start**: one manual `whisper-cli` run took 8.07 s wall time against 0.86 s of whisper processing, apparently a one-time Metal startup. This wasn't measured systematically. The servers are started once, so it affects startup, not per-turn latency.
- **Sample size**: n=40 ASR requests per model, 25 LLM requests and 25 TTS syntheses. That's enough for p50, but p95 is only roughly estimated.
- **Not measured yet** (Phase 1 metrics): voice-to-voice latency, VAD endpoint delay and barge-in reaction time. These need the live pipeline.
- **Disk space**: about 9.6 GB free after setup (models 1.7 GB, third_party about 0.5 GB, venv about 0.2 GB).
- **License**: `piper-tts` is GPL-3.0 (ADR 0003).

### Next: Phase 1 (EYWA Live core loop)
