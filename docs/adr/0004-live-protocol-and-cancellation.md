# 0004: Live audio protocol, cancellation ownership, and acknowledged history

Date: 2026-11-18. Status: implemented; combined real-model and human microphone validation pending.

Update 2026-11-19: real-model transport and browser-fixture interruption runs have
now completed. ADR 0005 supersedes the aggregate admission rule below. Human
microphone/echo validation remains separate. Piper now starts a speculative reload
after cancellation while the caller speaks, retaining per-load admission checks.

Update 2026-11-20: the user confirmed audible replies and successful physical
microphone interruption. Phase 1 is accepted with documented limitations;
quantitative echo quality and acoustic loopback latency remain unmeasured.

## Context

Phase 0 established the feasibility of resident native ASR/LLM servers. Phase 1
needs bounded audio capture, early playback cancellation, and evidence of which
assistant text actually played. Cancelling an asyncio task cannot interrupt an
in-process ONNX synthesis call.

## Decision

- Keep the native HTTP servers from ADR 0002. Close the LLM SSE response on
  cancellation. ASR HTTP cancellation discards the result; whisper-server may
  finish its current native decode. It cannot trigger downstream output.
- Move Piper from the proposed in-process location to a **resident owned Python
  subprocess**. Private newline-delimited JSON carries text and base64 PCM over
  stdio. Cancellation terminates and reaps that process; the next synthesis
  lazily reloads the voice with another admission check. Ordinary turns reuse it.
- Admit the combined model group using the **sum of isolated Phase 0 maxima**:
  1,313 + 322 + 76 + 223 MB. VAD and Piper now occupy separate processes, so the
  old combined 250 MB Python-worker measurement is no longer the right estimate.
  Available RAM already accounts for the running FastAPI process. Check every
  individual load too. Keep the 512 MB system reserve. The new process topology
  still needs its own combined memory measurement.
- Use one loopback FastAPI process, one call, one llama slot. Serve the Vite build
  from FastAPI for same-origin WebSockets, without an additional dev server.
- Capture mono PCM16 at 16 kHz in an AudioWorklet. Send exactly 512 samples per
  binary WebSocket message. Require browser echo cancellation. No browser API,
  model, script, font, or trace exporter accesses a cloud endpoint at runtime.
- Use Silero probabilities and a dedicated sample-clock endpointer: 96 ms onset,
  160 ms minimum speech, 480 ms silence, 15 s maximum, 192 ms pre-roll. Client
  energy detection can stop queued output earlier; the server independently
  detects speech and cancels inference. Thresholds are tunable in Python, not
  yet exposed in the UI.
- Stream LLM tokens through a bounded clause queue to Piper, then send 80 ms
  PCM chunks. Piper generates one sentence at a time internally. This is
  sentence-level synthesis, not sample-by-sample synthesis or streaming ASR.
- Fence every reply with a monotonically increasing generation ID. Interruptions
  cancel and await all pipeline tasks before another response can start. Failed
  sibling tasks cancel each other through an asyncio TaskGroup.
- Keep assistant history only after ordered `played` acknowledgements from the
  browser. The browser sends these from the last audio source's `onended` event
  for a clause. Stopping output invalidates those callbacks. Unknown, duplicate,
  out-of-order, and stale acknowledgements are ignored.
- Without reliable word/audio alignment for the selected voice, discard a
  partially played clause in full. **Completed clauses are retained; some heard
  words from an interrupted partial clause are lost.** Do not invent a text
  prefix by multiplying characters by playback time. This is a documented
  limitation until alignment is measured and validated.
- Keep session text/audio in RAM only, with bounded history. Persist OTel timing
  spans locally without transcripts, prompts, audio, or exception messages.

## Alternatives

- A cancellable Python thread around Piper leaves native synthesis running.
- Reloading Piper every sentence loses resident-model benefits.
- A shared cross-origin Vite dev server adds origin configuration and a process
  without helping the offline demonstration.
- Retaining all generated text on interruption makes history describe speech
  that the caller never heard. Estimated partial text would have the same risk.

## Tradeoffs and validation boundaries

Base64 adds overhead to outbound chunks; inbound microphone audio stays binary.
Cancellation during synthesis can require a voice reload on the next turn.
At most two completed, unacknowledged clauses are queued ahead of playback, plus
the bounded text queue. A 90 s turn timeout prevents an unresponsive client from
holding generation forever. A lost connection cancels work and clears history.

`server_voice_to_voice_ms` ends at first chunk emission on the server. The real
transport benchmark ends at receipt by a WebSocket client. Neither is acoustic
speaker onset. Its synthetic client consumes chunks immediately; it does not
measure human playback or interruption. Barge-in reaction time still requires a
timestamped speech-onset-to-output-stop run in a browser; client stop dispatch
time alone is not that metric.

Browser automation uses an explicit MockMicrophone (WebAudio MediaStream) and
MOCK inference. It tests the real AudioWorklet, PCM transport, playback callbacks,
and microphone-track cleanup. Native device acquisition in headless Chromium did
not resolve on this host, including its fake-device mode and a previous build;
that boundary and physical echo cancellation require a separate human check.
