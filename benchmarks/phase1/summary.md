# Phase 1 measured results

Generated from two separate real-model runs. No inferred or synthetic performance numbers.

- Transport report: `results/live-20261119T091314Z.json` (21 measured turns; warmup excluded).
- Browser report: `results/live-20261119T185653Z.json` (3 interruption/recovery cycles).
- Model profile: `balanced`; `models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf`.
- Hardware: Apple M2, 8 GiB RAM; macOS 26.6.2.
- Both reports include UTC date, commit, dirty-tree state, source hash, models, native builds, settings and raw observations.

| Measurement | n | p50 | p95 |
|---|---:|---:|---:|
| Transport speech-end → first audio received (ms) | 21 | 858.874 | 1119.284 |
| VAD endpoint delay (ms) | 21 | 480.000 | 480.000 |
| ASR processing (ms) | 21 | 211.284 | 297.462 |
| ASR real-time factor | 21 | 0.046 | 0.091 |
| LLM first token (ms) | 21 | 78.320 | 142.366 |
| LLM native tokens/sec | 21 | 64.411 | 65.668 |
| TTS first chunk (ms) | 21 | 45.164 | 133.045 |
| Browser recovered speech-end → WebAudio output scheduled (ms) | 3 | 998.700 | 1090.950 |
| Barge-in → playback stop dispatch (ms) | 3 | 32.300 | 32.390 |
| Server cancellation (ms) | 3 | 2.700 | 18.588 |

## Memory

Conservative sum of process maxima: **1.911 GB**, below the 2.5 GB target.

| Owned stage | Maximum RSS (MiB) |
|---|---:|
| piper | 210.77 |
| gateway | 80.59 |
| llama-server | 1246.61 |
| whisper-server | 284.47 |

Native/Piper maxima use macOS `/usr/bin/time -l`. Gateway peak uses `RUSAGE_SELF`
to avoid counting descendants twice. For replaced Piper workers, take the largest
worker peak, because the old process exits before its replacement starts. Raw time
output and 100 ms process-tree samples are saved in the browser report.

This sum covers backend/model processes. The existing browser and benchmark
driver are excluded; their pressure is reflected in system-memory samples.

## Interpretation

The browser uses known recorded caller audio through a synthetic WebAudio input
device. Silero, whisper.cpp, llama.cpp, Piper, the capture worklet, WebSocket
gateway and playback code are real. This is not physical microphone validation.

Browser output is a WebAudio schedule timestamp, not an acoustic speaker measurement.
Barge-in onset is the first captured RMS>0.025 frame; it has up to 32 ms frame
uncertainty and ends at playback-stop dispatch. Hardware output buffering is additional.
The browser rows describe responses after planned interruption and recovery.

These small fixture sets establish function and provide initial distributions;
they do not establish noise robustness, phone-quality accuracy, or broad conversational
quality. The two runs have distinct source hashes and are not pooled together.
