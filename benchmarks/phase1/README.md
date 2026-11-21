# Phase 1 evidence

## Current successful measurements (2026-11-19)

See [generated summary](summary.md). The default **balanced 1.5B profile** now has:

- [`results/live-20261119T091314Z.json`](results/live-20261119T091314Z.json):
  21 real-model audio turns after warmup. Transport latency p50/p95 is
  859 / 1,119 ms. This earlier run's gateway `/usr/bin/time` peak includes child
  accounting, so its summed memory field overcounts. Use the following run for
  corrected per-process memory measurement.
- [`results/live-20261119T185653Z.json`](results/live-20261119T185653Z.json):
  three transport turns plus three real browser interruption/recovery cycles.
  All three interrupted active generation and recovered in the same call.
  Backend peak-RSS upper bound is 1.911 GB. Browser post-interruption first-output
  scheduling p50/p95 is 999 / 1,091 ms. Playback-stop dispatch p50/p95 is
  32.3 / 32.4 ms, with up to 32 ms input-frame uncertainty. Physical device
  latency is not included.
- [`results/compact-20261119T161505Z.json`](results/compact-20261119T161505Z.json):
  compact 0.5B model profiled **alone**, before combined use. Measured peak was
  606.14 MiB. The live reserve guard did not trigger.
- [`results/live-20261119T163332Z.json`](results/live-20261119T163332Z.json):
  earlier compact pipeline/browser validation, with a 1.300 GB backend memory
  upper bound. This predates speculative Piper reload and the inactive-input
  silence fix; its slower post-interruption times are not the current default
  profile's published performance.

Reports marked `failed` preserve actual failures, including browser-probe
diagnostics. They are excluded from the successful summary. In particular,
`live-20261119T171033Z.json` retains the input-clock failure that led to the
capture-worklet silence regression test. It still contains its actual 21 completed
transport turns; those are not pooled into the published balanced-model table.

The real browser tests replace only the microphone device with known recorded
caller audio. All inference models and production capture/transport/playback code
are real. On 2026-11-20 the user separately confirmed audible replies and successful
interruption with the physical microphone/speaker path. That manual report adds
no timing measurements or quantitative echo-quality claims. No raw user recordings
are stored.

The memory sum covers the **backend and model processes**, excluding the existing
browser and benchmark driver. Native/Piper values use `/usr/bin/time -l`; gateway
uses `RUSAGE_SELF` so descendants are not counted twice. Replaced Piper workers
do not overlap; the largest worker peak is used. Raw time output is included in
the final report, alongside 100 ms process-tree/system-memory samples.

## Earlier evidence and methodology

`make check-adapters` exercises the real production adapters **one at a time**.
`make bench-phase1` attempts the whole real-model WebSocket loop and saves an
honest refusal record if admission fails. It never falls back to mocks.

Current results include:

- [`results/adapters-20261118T144016Z.json`](results/adapters-20261118T144016Z.json):
  real Piper cancellation/reload, real whisper.cpp transcription, real llama.cpp
  stream-close cancellation and a successful following request. Each model was
  loaded alone. Timing fields are single observations, not distributions.
- [`results/live-20261118T083008Z.json`](results/live-20261118T083008Z.json): initial
  combined admission refusal using the previous grouped Python-worker threshold.
- [`results/live-20261118T160438Z.json`](results/live-20261118T160438Z.json): combined
  refusal after updating admission for isolated VAD and Piper processes. No
  combined inference ran and `runs` is empty.

All reports identify their commit, dirty-tree state, code hash, hardware, pinned
native builds and expected model SHA-256 values from the download manifest.
These runs use uncommitted implementation files, as `git_dirty` explicitly states.

The transport harness paces existing synthetic caller PCM frames every 32 ms.
Its primary latency boundary is last Silero-positive speech frame **sent** to
first synthesized audio **received**. It immediately consumes generated chunks,
so it does not measure audible speaker onset, human speech, echo, or barge-in.
Warmup is recorded separately and excluded from summaries. Quantiles are computed
only from actual successful real-model turns. The default is 21 measured turns;
increase rounds before making strong tail-latency claims.

Memory is sampled across the server process tree every 100 ms. It is labelled
**sampled peak RSS**, not exact process peak. No peak RSS for the new combined
topology is available while admission is blocked.

The earlier admission-refusal files document the previous guard and changing
machine pressure. ADR 0005 records why admission now checks each next model
against current memory while retaining the same 512 MB reserve.
