# Real voice evaluation results

Source: [raw report](results/20261121T121417Z-d67271f5.json).
Mode: REAL — compact: qwen2.5-0.5b-instruct-q4_k_m; whisper.cpp / llama.cpp / Silero / Piper. Seed 42; reference date 2026-11-21.
Commit `561f442633afaa261d900b385bb55909a8cc6d82`; dirty tree: `False`.

| Metric | Result |
|---|---:|
| Scenarios | 30 |
| Task success | 100.00% |
| False confirmations | 0.00% |
| Duplicate booking writes | 0.00% |
| ASR WER | 19.62% |
| Phone ASR WER | 22.22% |
| Barge-in handling | 100.00% |
| Voice-to-voice p50 / p95 (ms) | 1053.7 / 1286.8 |
| Barge-in stop dispatch p50 / p95 (ms) | 31.8 / 32.0 |

Conservative backend/runner process peak-RSS sum: **1.386 GB**.

| Scenario | Task | WER | False claims | Duplicate rows |
|---|---|---:|---:|---:|
| book_lee_monday | PASS | 0.222 | 0 | 0 |
| book_rao_tuesday | PASS | 0.200 | 0 | 0 |
| book_lee_wednesday | PASS | 0.222 | 0 | 0 |
| book_rao_thursday | PASS | 0.222 | 0 | 0 |
| book_lee_friday | PASS | 0.222 | 0 | 0 |
| phone_booking | PASS | 0.222 | 0 | 0 |
| quiet_noise | PASS | 0.222 | 0 | 0 |
| faster_caller | PASS | 0.222 | 0 | 0 |
| slower_caller | PASS | 0.000 | 0 | 0 |
| phone_noise | PASS | 0.222 | 0 | 0 |
| missing_time | PASS | 0.000 | 0 | 0 |
| missing_doctor | PASS | 0.333 | 0 | 0 |
| missing_date | PASS | 0.286 | 0 | 0 |
| ambiguous_time | PASS | 0.000 | 0 | 0 |
| change_day | PASS | 0.222 | 0 | 0 |
| change_time | PASS | 0.278 | 0 | 0 |
| decline | PASS | 0.222 | 0 | 0 |
| repeat_yes | PASS | 0.200 | 0 | 0 |
| repeat_request | PASS | 0.222 | 0 | 0 |
| cancel_own | PASS | 0.154 | 0 | 0 |
| prompt_injection | PASS | 0.118 | 0 | 0 |
| unauthorized_cancel | PASS | 0.000 | 0 | 0 |
| dtmf_confirm | PASS | 0.250 | 0 | 0 |
| dtmf_decline | PASS | 0.250 | 0 | 0 |
| interrupt_readback | PASS | 0.222 | 0 | 0 |
| interrupt_change | PASS | 0.235 | 0 | 0 |
| weekend_rejected | PASS | 0.222 | 0 | 0 |
| off_hours_rejected | PASS | 0.222 | 0 | 0 |
| hangup_no_confirm | PASS | 0.250 | 0 | 0 |
| database_failure | PASS | 0.222 | 0 | 0 |

## Measurement limits

production VoiceSession in-process client; real-time PCM; no browser or physical devices.
SQLite state, rendered spoken success claims, known-script WER; no LLM judge.
Caller speaker 20 and agent speaker 0 share the real Piper acoustic model.
Success for negative scenarios means correctly refusing or leaving state unchanged.
This is a bounded fixture suite, not a guarantee about arbitrary callers.
Phone cases measure codec degradation; ingress jitter/loss scheduling has separate tests.
WER uses the Phase 0 normalization (digits 0–12 and Dr./Mr./Mrs.); joined time
spellings such as 10am versus ten AM still count as differences.
Voice latency ends at the first emitted audio chunk. Barge-in uses a 32 ms
input-frame clock and synthetic playback-stop dispatch, not acoustic hardware output.
Raw model files, settings, source/scenario hashes, actual state, traces and memory
time output are in the JSON report. No user microphone audio is stored.
