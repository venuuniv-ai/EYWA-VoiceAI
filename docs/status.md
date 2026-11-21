# EYWA status

## Remaining-phase implementation update (2026-11-21)

At the user's request, the remaining phases were implemented before the final
test pass. Connect, the 30-scenario real audio evaluator, dashboard/report replay,
setup documentation, GPL licensing/attribution and a labelled recorded demo are
implemented and validated. All phases are accepted within the documented local,
single-clinic scope. The final real run passed 30/30 scenario expectations with
zero false confirmations or duplicate writes. See the final acceptance entry below.

The repository has been created privately at `venuuniv-ai/EYWA-VoiceAI`; model
weights, dependencies, databases, caller cookies and runtime audio are excluded
from Git. Only synthetic demonstration assets and benchmark JSON are included.

## Phase 0: Environment and feasibility (2026-11-03)

All numbers below come from `benchmarks/phase0/summary.md`, which is generated from
`benchmarks/phase0/results/phase0-20261103T092517Z.json` (commit `a4e7251`, clean tree). An earlier run before the first commit, `phase0-20261103T091808Z.json`, gave numbers within about 10% of these.

### Verified
- **Environment**: Apple M2, 8 GB, macOS 26.6.2, Homebrew 7.0.8, Node v26.10, git 2.56, FFmpeg 9.0.2 and CMake 4.4.5 (both installed in this phase), project venv on Python 3.11.17 (ADR 0001).
- **Native builds**: whisper.cpp v1.9.5 and llama.cpp b12066 built with Metal (`scripts/setup_native.sh`). The GPU is detected as `MTL0 (Apple M2)`.
- **Models**: downloaded by `scripts/download_models.py` from pinned commits/tags, and every file passed SHA-256 verification.
- **Each model measured alone** (max RSS; p50):
  - Silero VAD: 76 MB; 0.10 ms per 32 ms frame
  - Piper libritts_r: 223 MB; first chunk 37 ms; RTF 0.029
  - whisper base.en: 322 MB; 176 ms per request
  - whisper small.en: 680 MB; 430 ms per request
  - Qwen2.5-1.5B Q4_K_M (ctx 4096): 1,300 MB; TTFT 101 ms; 62.0 tok/s
- **All models together**, running a real VAD → ASR → LLM → TTS sequence: the sum of max RSS was **1,841 MB with base.en** (no swap growth) and **2,229 MB with small.en** (swap grew by 282 MB). Both are under the 2.5 GB target.
- **Admission check**: `pipeline/admission.py` refuses to load a model when it would leave less than 512 MB available, and gives the reason.
- **Tests**: `make test` gives 13 passed. They cover the admission check, checksum-verified downloads (including rejecting a tampered file), WER, and the Silero wrapper on real audio.

### Decisions
- **Default ASR: `base.en`.** Both models had a normalized WER of 0 on the four clean clips, but base.en is about 2.4× faster (176 vs 430 ms p50), uses about 360 MB less, and was the only configuration that ran without swap growth. Re-test small.en on phone-quality μ-law audio in Phase 2.
- ASR and LLM run as resident native servers (ADR 0002). The TTS voice and its license are covered in ADR 0003.
- Admission thresholds to use in Phase 1, from measured max RSS: LLM 1,313 MB; ASR base.en 322 MB; TTS+VAD process 250 MB.

### Limitations and open items
- **Less free memory than the spec assumed**: only 2,552 MB was available at the start of the run, below the 3–4 GB the spec assumed. The lowest system available memory during the combined run was 842–1,080 MB, and the small.en combination pushed 282 MB into swap. EYWA fits, but closing apps changes the result.
- **The memory sum is an upper bound**: it counts the mmap'd GGUF pages and counts shared libraries once per process. Peak footprint, which leaves out clean mmap'd pages, was 707 / 1,162 MB.
- **The WER test is easy**: three of the four ASR clips are Piper-synthesized speech and the fourth is the whisper.cpp JFK sample. These are clean audio, not real callers or phone codec, so WER=0 here says little about real-world accuracy.
- **LLM TTFT used a short prompt** (about 60 tokens). Prompt processing runs at about 675 tok/s (llama-bench pp256), so a 1,000-token system prompt with tool schemas would add roughly 1.5 s of prefill per uncached turn. Prompt caching in llama-server needs to be measured in Phase 1.
- **Cold start**: one manual `whisper-cli` run took 8.07 s wall time against 0.86 s of whisper processing, apparently a one-time Metal startup. This wasn't measured systematically. The servers are started once, so it affects startup, not per-turn latency.
- **Sample size**: n=40 ASR requests per model, 25 LLM requests and 25 TTS syntheses. That's enough for p50, but p95 is only roughly estimated.
- **Not measured yet** (Phase 1 metrics): voice-to-voice latency, VAD endpoint delay and barge-in reaction time. These need the live pipeline.
- **Disk space**: about 13 GB free after setup (the user freed space during this phase) (models 1.7 GB, third_party about 0.5 GB, venv about 0.2 GB).
- **License**: `piper-tts` is GPL-3.0 (ADR 0003).

### Next: Phase 1 (EYWA Live core loop)

## Phase 1: EYWA Live implementation (2026-11-18)

**In progress. Not accepted as a complete phase.** No Connect or Eval work started.

### Verified

- `make test`: **56 passed**, including TypeScript/Vite production build and three
  Playwright browser tests. One upstream Starlette/httpx deprecation warning is
  reported; it does not affect the assertions.
- The production Silero wrapper and endpointer process the recorded human JFK
  fixture. Endpoint unit tests cover silence, brief noise, minimum speech, silence
  gaps, maximum turn duration, and invalid settings.
- Explicit MockASR/FakeLLM/MockTTS tests exercise cancellation during each stage,
  sibling-task failure, disconnect, bounded queues, empty synthesis, stale and
  duplicate playback acknowledgements, and discarding unplayed assistant text.
- Browser automation uses **MockMicrophone + MOCK inference**, identified in the
  startup log and UI. It exercises the production AudioWorklet and PCM WebSocket
  transport, echo-cancellation request, microphone track cleanup, cancellation of
  pending permission acquisition, late-stream cleanup, and audio-stop/onended
  acknowledgement races. This does **not** validate a physical microphone.
- The new **real adapters were exercised separately**, with admission before each
  load. Raw results:
  [`adapters-20261118T144016Z.json`](../benchmarks/phase1/results/adapters-20261118T144016Z.json).
  Piper's real worker was cancelled before first audio was ready, exited, then
  reloaded and synthesized successfully. whisper.cpp transcribed the known caller
  fixture through the production WAV-upload adapter. llama.cpp had an active slot
  before the HTTP stream closed, became idle afterwards, and served a following
  request. These are functional checks with single observations, not p50/p95 or
  end-to-end latency measurements.
- `make bench-phase1` preflight refused to load the combined model group. The
  later refusal record is
  [`live-20261118T160438Z.json`](../benchmarks/phase1/results/live-20261118T160438Z.json):
  1,276 MB available versus 1,934 MB measured isolated-model requirements plus
  512 MB reserve. `runs` is empty; **no combined timing numbers were generated**.
  The user chose to continue with this block documented.
- No owned inference or test-browser processes remained after validation.

### Implemented, awaiting combined real-model / human validation

- Resident native ASR/LLM adapters; resident Piper subprocess with native-work
  termination and reload after cancellation; per-load and whole-group admission.
- Loopback FastAPI gateway; one concurrent call; same-origin checks; bounded
  16 kHz mono PCM16 transport; generation IDs fence stale output.
- React microphone client with echo cancellation, local playback stop, queued
  audio playback and ordered playback acknowledgements. Only completed played
  clauses enter bounded conversation history.
- Bounded token-to-clause-to-audio streaming, task cancellation, 90 s turn timeout,
  and local OTel spans per turn/stage without speech content or exception text.
- `make dev`, `make test`, `make check-adapters`, and `make bench-phase1`, plus
  setup/run documentation and ADR 0004.

### Remaining acceptance work and limitations

1. Run the **combined** real-model browser loop when sufficient RAM is available.
   Measure the new FastAPI + VAD / Piper-worker process topology against the
   2.5 GB target; Phase 0's grouped Python-worker measurement cannot validate it.
2. Record p50/p95 from real audio-in/audio-out turns. The new harness measures
   paced PCM speech-end to first WebSocket audio receipt and clearly distinguishes
   that from speaker onset. Benchmark prompt caching across multi-turn calls;
   the current transport harness starts a fresh session for each fixture.
3. Validate physical microphone capture and echo cancellation. Native microphone
   acquisition did not resolve in this host's headless Chromium fake-device mode,
   including a previous installed build. Automation uses an explicit WebAudio
   mock microphone instead. The pending-permission path is cancellable in the UI.
4. Measure speech-onset-to-playback-stop barge-in reaction time and verify it with
   real overlapping speech. The independently verified native cancellation paths
   are necessary but do not establish full end-to-end barge-in behavior.
5. Interrupted **partial clauses are discarded in full**: completed heard clauses
   are retained, but heard words within a partial clause are currently lost.
   The selected voice has no validated word/audio alignment for exact truncation.
6. ASR cancellation closes the HTTP request and fences its result, but the native
   decode may finish. Native server crashes produce a pipeline error; restarting
   `make dev` is currently required (no automatic process restart).
7. Raw audio recording and transcript persistence are not implemented. Timing-only
   traces are local; files older than seven days are removed on startup. There is
   no external telemetry exporter.
8. The npm production dependency audit is clean. Build-time Vite dependencies
   inherit an unpatched picomatch advisory at the current registry versions; the
   Vite dev server is not used. See README for this setup limitation.

### Next

Complete the Phase 1 acceptance measurements above before beginning Phase 2's
single fictional appointment-booking workflow. Claims intake, escalation, and
additional domains remain deferred.

## Phase 1 validation update (2026-11-19)

**Real-model software loop validated; physical microphone/echo check pending.**
The memory block described in the previous entry has been resolved in code and
verified with real runs. The existing app is running at `http://127.0.0.1:8000`
on the default balanced 1.5B profile for the human check. Later phases remain gated.

### Verified

- `make test`: **62 passed**, including the production build and five Playwright
  tests. The one upstream Starlette/httpx deprecation warning remains.
- All four models load sequentially, with the original **512 MB reserve** checked
  before every load. Tests verify that a refusal after the first model reaps that
  earlier process. No force-load switch and no silent mock/model fallback exist.
- The default real 1.5B pipeline completed **21 measured audio turns**, excluding
  warmup: first WebSocket audio receipt **859 ms p50 / 1,119 ms p95**. Raw report:
  [`live-20261119T091314Z.json`](../benchmarks/phase1/results/live-20261119T091314Z.json).
- The final real default-profile browser run completed **3/3 planned interruption
  and recovery cycles**, with recorded caller input through a synthetic WebAudio
  device. All inference, the AudioWorklet, gateway and playback were real. Every
  cycle cancelled active generation and produced a subsequent response in the
  same call. Browser first-output scheduling after interruption was **999 ms p50 /
  1,091 ms p95**. Stop dispatch was **32.3 ms p50 / 32.4 ms p95**, with up to
  **32 ms input-frame uncertainty**, excluding acoustic hardware buffering.
- The same final run's conservative sum of backend process peak RSS was
  **1.911 GB**, below the 2.5 GB target. Gateway uses `RUSAGE_SELF`; native/Piper
  processes use macOS `/usr/bin/time -l`. The minimum sampled system available
  memory was **935 MiB**, including pressure from the browser and driver. Raw data:
  [`live-20261119T185653Z.json`](../benchmarks/phase1/results/live-20261119T185653Z.json).
- Piper reload now starts during the next caller utterance after cancellation,
  retaining admission checks. Tests cover duplicate speculative reload requests,
  cancellation on shutdown, and refusal without blocking the next requested load.
- Inactive input buses now send silence frames, keeping endpointing's sample clock
  moving. A real browser probe exposed this and a regression test covers it.
- Buffered WebSocket events after End call cannot resurrect playback or update
  the ended conversation; a browser regression test verifies this cancellation path.
- In-call memory refusals report their actual memory requirement/reserve; other
  provider exception strings remain excluded from messages and traces.
- An explicit `compact` 0.5B profile is installed and checksum-verified. It was
  profiled alone first: **606.14 MiB peak RSS**, then exercised in the real pipeline
  and browser (earlier code revision). Its 640 MiB admission budget includes
  margin above observed usage. It does not replace the default automatically.
- npm production dependency audit: zero vulnerabilities. `AGENTS.md` and
  `CLAUDE.md` remain identical. The backend is left running intentionally for the
  requested physical microphone check; benchmark-owned processes were reaped.

All published tables are generated from raw reports in
[`benchmarks/phase1/summary.md`](../benchmarks/phase1/summary.md). ADR 0005 records
the measured admission policy and explicit model profiles. Failed runs remain
saved and are excluded from successful tables.

### Still not verified / deliberately limited

- **Physical microphone and speaker/echo behavior.** The user has been asked to
  speak and interrupt through the running UI. Browser fixture results do not
  establish this hardware boundary; Phase 1 is not declared fully accepted yet.
- **Exact acoustic latency.** Recorded values end at transport receipt or WebAudio
  scheduling/stop dispatch. A physical loopback measurement would include device
  buffering. Initial p95 values have small sample counts.
- Partial spoken clauses are still discarded in full from assistant history,
  retaining only completed heard clauses. No unspoken generated text is kept.
- ASR may finish native decoding after its HTTP caller is cancelled; its stale
  result is discarded. Native server crashes still require an application restart.
- The compact profile has less model capacity; no claim of equivalent reasoning
  or booking accuracy is made. Phone/noise accuracy, full multi-turn cache-growth
  profiling, and Connect/Eval acceptance remain future work.

## Phase 1 accepted (2026-11-20)

**EYWA Live core-loop acceptance criteria are met, with the limitations above.**

- The user confirmed **“Reply and interruption both worked”** after being asked
  to speak through the running browser UI and interrupt its reply. This verifies
  the functional physical microphone/speaker path by user report; no audio was
  recorded and no new timing numbers are inferred from the report.
- The server was restarted on the explicit **compact Qwen2.5-0.5B Q4_K_M** profile
  because the balanced model was correctly refused under current memory pressure.
  Health at recording time identified real compact inference. The balanced-model
  latency and memory results remain separately labelled in the benchmark reports;
  they are not attributed to this manual compact-profile check.
- Existing software evidence remains: **62 passing tests**, real-model pipeline
  measurements, bounded backend RSS, and real browser cancellation/recovery runs.
- Quantitative echo suppression, acoustic loopback latency, broader audio quality,
  exact partial-clause history and the other documented limitations remain open.
  The manual success is not a claim that those measurements have been performed.

Phase 2 (EYWA Connect) is now eligible to begin. It has not been implemented or
started by this status update. The app remains running for the user's current call.

## Phases 2–5 accepted (2026-11-21)

Final local checks: **107 tests passed**, including seven Playwright browser
tests, plus the TypeScript/Vite production build. Dependency consistency and
`git diff --check` pass. The single upstream Starlette/httpx deprecation warning
is retained. It does not affect the assertions.

### Phase 2 — verified business actions and telephone simulation

- Fictional Willowmere Demo Clinic booking/cancellation uses collect → confirm →
  commit → report. Complete playback acknowledgement plus an explicit affirmation
  or DTMF 1 is required. Intervening requests invalidate prior confirmation.
- Schema-constrained local LLM output is Pydantic-validated and field-grounded.
  SQLite writes are transactional, authorized and idempotent. Concurrent retries,
  repeated semantic requests, key conflicts, superseded writes, foreign record
  access, injected commit failures and stale read-backs have passing tests.
- Real G.711 encode/decode, low-pass/resampling, DTMF PCM detection, bounded seeded
  ingress delay/jitter/loss, and simulated call states are implemented. Held audio
  is dropped; delayed packets from an earlier hold epoch cannot reappear on resume.
- Real phone-quality ASR WER is measured in the final suite: **22.22%** under the
  published normalization, including formatting differences such as 10am/ten AM.
  It is not presented as perfect transcription accuracy.

### Phase 3 — real audio-in/audio-out evaluation

Final report:
[`20261121T121417Z-d67271f5.json`](../benchmarks/phase3/results/20261121T121417Z-d67271f5.json).
It ran from clean commit `561f442633afaa261d900b385bb55909a8cc6d82` with seed 42,
reference date 2026-11-21, real compact Qwen/whisper.cpp/Piper/Silero models, and
caller speaker 20 versus agent speaker 0. The subsequent changes tighten model
admission budgets based on observed peaks and discard held ingress packets; they
do not change these recorded model outputs or timing observations.

| Metric | Actual final result |
|---|---:|
| Unattended scenarios | 30 |
| Task expectations met | 30/30 |
| False confirmations | 0 of 19 outgoing success claims |
| Duplicate booking writes | 0 |
| Mean known-script ASR WER | 19.62% |
| Mean phone-case ASR WER | 22.22% |
| Voice-to-voice p50 / p95 | 1,054 / 1,287 ms |
| Planned interruption handling | 2/2 |
| Synthetic playback-stop p50 / p95 | 31.8 / 32.0 ms |
| Conservative backend/runner peak-RSS sum | 1.386 GB |

Raw per-scenario state, expectations, transcripts, stage metrics, perturbations,
model/source/scenario hashes and native memory output are in the JSON. Earlier
93.3% and 96.7% runs are retained, including the failures that produced the
confirmation-variant and stale-correction fixes. No expectations were weakened.
The generated table and measurement limits are in
[`benchmarks/phase3/summary.md`](../benchmarks/phase3/summary.md).

The full workload exposed higher peaks than short isolated prompts: compact LLM
640.875 MiB and Piper approximately 291 MiB. Current admission budgets are **680
MiB / 320 MiB**, including margins; the **512 MiB reserve is unchanged**. These
policy values are not substituted for the measured RSS results.

### Phase 4 — dashboard

- React provides live/clinic modes, phone controls, call states, spoken transcript,
  per-turn timings, pending/verified booking state, DTMF and local record display.
- The report browser shows aggregate metrics, every scenario's state and trace,
  downloadable JSON, and synthetic conversation replay. Evaluation start/stop and
  progress share the one-call resource slot; only the initiating caller can stop it.
- Playwright covers capture/stop cleanup, stale socket events, silent input clocks,
  report browsing, scenario replay controls and simulated phone/hold controls.
- A real browser booking was recorded and checked for completed read-back,
  committed state and played success audio. It is labelled synthetic caller input,
  with real local inference. Physical mic/reply/interruption was separately
  confirmed by the user in Phase 1.

### Phase 5 — reproducible portfolio delivery

- README includes offline setup, one-command start/tests, profiles, supported
  workflow, architecture diagram, evaluation, privacy and explicit limitations.
- Python transitive constraints, npm lockfile, pinned native builds and verified
  model SHA-256 downloads are included. Models/dependencies/runtime data are ignored.
- GPL-3.0-or-later LICENSE and third-party/LibriTTS-R attribution are recorded.
- [`docs/demo.mp4`](demo.mp4) contains H.264 video and AAC audio from the real
  synthetic-caller booking session; [`docs/demo.png`](demo.png) provides a preview.
  PCM is reconstructed for the labelled recording, not captured from a human mic.
- The private GitHub repository is `https://github.com/venuuniv-ai/EYWA-VoiceAI`.

### Limits retained after acceptance

This is one fictional appointment domain, one concurrent call/eval, and restricted
date/time language. No real PSTN, human transfer, real-world identity assurance,
claims intake, or medical advice is provided. Exact partial-word playback history,
quantitative echo/acoustic latency, broad real-caller/noise performance and automatic
native-server crash recovery remain limitations, not completed features. Evaluation
uses a paced in-process VoiceSession client; separate real browser tests cover the
WebSocket/device-replacement path. All published claims are scoped to their fixtures.
