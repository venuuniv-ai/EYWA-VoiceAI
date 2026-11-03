# EYWA — Project Instructions for Coding Agents

This file is read automatically by Claude Code (`CLAUDE.md`) and OpenAI Codex (`AGENTS.md`). Both files hold the same content; keep them in sync.

## What EYWA is

A local-first, real-time voice AI platform built as a portfolio project. Three subsystems plus a dashboard:

1. **EYWA Live**: real-time voice conversations (mic → VAD → ASR → LLM → TTS → speaker) with interruption (barge-in) support.
2. **EYWA Connect**: verified business workflows over a simulated phone line.
3. **EYWA Eval**: automated, audio-in/audio-out testing of the voice agent with synthetic callers.
4. **Dashboard**: React web UI for live calls, per-turn traces, and eval results.

**EYWA is not NOVA with a voice.** The developer's previous project (NOVA) covers local LLM inference, RAG, agents, source grounding and resource controls. Do not add RAG, document retrieval or generic chat-assistant features. EYWA's technical focus is streaming audio, speech processing, turn-taking, telephony simulation, verified actions and conversational evaluation.

## Environment and hard constraints

- Apple Silicon M2, **8 GB unified RAM**, macOS, VS Code.
- **$0 budget**: no paid APIs or cloud services. Essential features must never need a paid API.
- **Offline after setup**: once dependencies and models are installed, the core system must work without internet access.
- Local-first deployment, new independent Git repo.
- **Assume about 3–4 GB is available to EYWA.** VS Code, a browser and coding agents will be running alongside it.
- **No Docker for inference.** Docker on macOS cannot reach the Metal GPU. Run whisper.cpp and llama.cpp natively. Docker is optional and only for non-inference tooling.
- No Kubernetes, Kafka, Redis or other infrastructure added to raise the technology count. SQLite by default. Use PostgreSQL only if a documented need arises.

## Stack (initial choices; change only with a recorded reason)

| Concern | Choice | Notes |
|---|---|---|
| Backend | Python, FastAPI, asyncio | |
| Frontend | React + TypeScript (Vite) | |
| Transport | WebSocket, 16 kHz mono PCM16 | Add WebRTC only if a measurement justifies it |
| VAD | Silero VAD | |
| ASR | whisper.cpp (`base.en`, try `small.en`), Metal build | VAD-segmented chunks; not true streaming |
| LLM | llama.cpp, small quantized Qwen (~1.5B, Q4) | Use grammar/JSON-schema constrained output for tool calls |
| TTS | Piper | Check each voice's license |
| Audio tooling | FFmpeg | |
| Storage | SQLite | |
| Tracing | OpenTelemetry | One span per pipeline stage per turn |
| Tests | Pytest, Playwright | |

## Memory budget (must be measured, not assumed)

Target: all models loaded together, **EYWA peak RSS under 2.5 GB**. Expected approximate sizes, to be checked:

- whisper.cpp base.en/small.en: ~150–500 MB
- Qwen ~1.5B Q4 + KV cache: ~1.0–1.3 GB
- Piper voice: ~60–100 MB
- Silero VAD: very small

Measure each model alone before loading them together. Add an admission check that refuses to load a model when free memory is below a set threshold, and report the reason clearly.

## Latency metrics (define first, then measure)

Record p50 and p95 from real runs and store the raw results in `benchmarks/`. **Never invent or estimate numbers.**

- **Voice-to-voice latency**: time from end of user speech to first agent audio. This is the headline metric.
- VAD endpoint delay
- ASR processing time and real-time factor
- LLM time to first token, and tokens/sec
- TTS time to first audio chunk
- **Barge-in reaction time**: time from user speech onset during agent speech to playback stopping

Every benchmark records the hardware, model files and quantization, settings, date and git commit.

## Core behaviors to get right

### Turn-taking
Endpointing is its own module (`turn/`), with tunable parameters (silence threshold, minimum speech duration, maximum turn length) and dedicated tests built from recorded audio fixtures.

### Interruption semantics (barge-in)
When the user speaks while the agent is speaking:
1. TTS playback stops on the client right away.
2. In-flight LLM generation and TTS synthesis are cancelled server-side.
3. Only the portion of the reply actually played is kept in conversation history. Unspoken text is discarded.
4. Audio capture uses the browser's echo cancellation (`echoCancellation: true`), so the agent does not hear itself.

Every cancellation path has a test.

### Streaming
Prefer streaming over buffering: stream LLM tokens into TTS at sentence or clause boundaries, and stream TTS audio to the client in chunks.

## EYWA Connect: verified business workflows

- **One concrete domain**: appointment booking for a fictional clinic (or similar; decide in an ADR). Use a clearly fictional business name.
- The workflow is an **explicit state machine**: collect → confirm → commit → report.
- **Spoken read-back before commit**, e.g. "Booking Tuesday at 3 pm with Dr. Lee — is that right?" No commit until the user confirms.
- **Idempotency key on every write.** Retries and repeated user requests must never create duplicate records.
- **Authorization checks** on every operation. A caller cannot read or modify records that aren't theirs.
- **Announce success only after the backend confirms the write.** On failure, say so honestly.
- Tool calls use grammar-constrained JSON. Validate every argument against a schema before execution.

### Telephony simulation
- Downsample to 8 kHz μ-law (G.711) and back, so ASR runs on phone-quality audio.
- Configurable injected jitter, packet loss and latency.
- DTMF tone input (for example, "press 1 to confirm").
- Call states: ringing, connected, hold, transfer, hangup.
- **Scope: one concurrent call.** State this limit in the docs. Multiple simultaneous calls are out of scope for 8 GB.

## EYWA Eval: the differentiator

- **Synthetic callers**: scripted scenarios are spoken with TTS (using a different voice from the agent's) and sent through the full audio pipeline. This is audio-in/audio-out, not text-only.
- **Perturbations**: background noise, speed changes, phone codec, planned interruptions mid-reply, mid-call changes of mind, invalid requests.
- **Score against backend state**, not an LLM judge. For example: was exactly one booking created, with the correct fields?
- Metrics per scenario and in aggregate:
  - task success rate
  - **false confirmation rate** (agent claimed success that didn't happen)
  - duplicate-write rate
  - ASR WER on known scripts
  - voice-to-voice latency
  - barge-in handling success
- Scenarios are version-controlled files. Runs are reproducible with a fixed seed and saved as JSON reports that the dashboard displays.

## Privacy and security

- Raw audio is **not stored by default**. Storing it is an explicit opt-in setting with a retention limit.
- Redact PII in stored transcripts and logs (phone numbers, dates of birth, etc.).
- No telemetry leaves the machine. OpenTelemetry exports go to a local backend or file only.
- Model files are downloaded by a script with pinned versions and SHA-256 checksums.

## Engineering rules

1. Build working functionality before adding complexity.
2. Keep the architecture modular and testable: each pipeline stage sits behind an interface, so mocks can be swapped in.
3. **Mocks must be clearly identified.** Use names like `MockASR` and `FakeLLM`, and log `MOCK` at startup. Never silently replace real inference with a mock. Any benchmark or demo that uses a mock must say so.
4. Profile memory before loading multiple models.
5. Write tests alongside the code. Run them. Investigate failures. Don't skip or weaken a test to make it pass.
6. **Never call a feature complete without validation.** "Done" means implemented, tested, tests passing, and limitations written down.
7. Record architecture decisions in `docs/adr/NNNN-title.md`: context, decision, alternatives, tradeoffs.
8. Keep it understandable: plain Python, few dependencies, no clever abstractions without a reason.
9. Provide one command to start the whole system (`make dev` or a script) and one command to run all tests.

## Phased plan

Each phase ends with: tests passing, benchmarks recorded in `benchmarks/`, limitations documented, and a short status note in `docs/status.md` saying what is complete and what remains.

**Phase 0: Environment and feasibility**
- Check the environment (macOS version, Python, Node, Homebrew, FFmpeg, free RAM).
- Build whisper.cpp and llama.cpp with Metal. Download models with checksums.
- Benchmark each model alone: memory, load time, speed.
- *Accept when:* each model runs locally with recorded memory and speed numbers, and the combined budget is shown to fit.

**Phase 1: EYWA Live core loop (MVP)**
- Browser mic → WebSocket → VAD → ASR → LLM → TTS → browser playback.
- Turn-taking module, barge-in with full cancellation, streamed TTS.
- Per-stage OpenTelemetry spans.
- *Accept when:* a live conversation works end-to-end with real models, barge-in stops playback and generation (covered by tests), and voice-to-voice p50/p95 is recorded.

**Phase 2: EYWA Connect**
- Booking backend (SQLite), state machine, read-back confirmation, idempotency, authorization, constrained tool calls.
- Telephony simulator (μ-law, jitter, loss, DTMF, call states).
- *Accept when:* tests show no duplicate writes under retries, no success announced without a confirmed write, and unauthorized access refused. ASR WER on phone-quality audio is measured.

**Phase 3: EYWA Eval**
- Scenario format, synthetic caller generator, perturbations, state-based scoring, JSON reports.
- *Accept when:* a suite of 30 or more scenarios runs unattended and the report shows every metric listed above, from real runs.

**Phase 4: Dashboard**
- Live call view with transcript, per-turn latency breakdown, booking state, and eval result browser.
- Playwright end-to-end tests.

**Phase 5: Portfolio polish**
- README with setup and run instructions, architecture diagram, benchmark tables from `benchmarks/`, documented limitations, and a recorded demo video.

## Repo layout (suggested)

```
eywa/
  server/        # FastAPI app, WebSocket audio gateway
  pipeline/      # vad/, asr/, llm/, tts/ — each behind an interface
  turn/          # endpointing + barge-in logic
  connect/       # business domain, state machine, tools, telephony sim
  eval/          # scenarios/, caller generator, scorer, reports
  web/           # React dashboard
  scripts/       # model download (pinned + checksums), setup
  benchmarks/    # raw results + summaries
  docs/          # architecture, adr/, status.md
  tests/
```

## Working style for agents

- Work one phase at a time. Don't start the next phase until the current one meets its acceptance criteria.
- Before a large change, briefly state the plan and the files it touches.
- When reporting, separate clearly what was **verified** (with test or benchmark output) from what was **implemented but not yet validated**.
