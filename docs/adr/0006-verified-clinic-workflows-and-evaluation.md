# 0006: Verified fictional appointments and audio evaluation

Date: 2026-11-21. Status: implemented; validation occurs after remaining phase implementation at the user's request.

## Context and decision

Use one domain, **Willowmere Demo Clinic**, with Doctors Lee and Rao and six
weekday hourly slots within thirty days. This is fictional scheduling, not a
medical consultation or real clinic integration. Claims intake is deferred to
keep the single-domain scope in AGENTS.md.

The state machine collects grounded date/time/doctor fields, speaks a complete
read-back, waits for its playback acknowledgement, and requires an explicit yes
or DTMF 1. Only then does a SQLite transaction commit. Success speech is rendered
from the committed row, never from unconstrained model text. Changes of mind
replace the pending challenge; interrupted read-backs cannot authorize a write.

llama.cpp uses JSON-schema constrained output for the extraction call. Pydantic
validates the result again. Date/time/doctor arguments must be grounded in the
actual recognized utterance. Supported weekday and AM/PM expressions are
resolved deterministically in America/New_York; ambiguity prompts clarification.
No model chooses whether a database write is authorized.

Each browser receives an opaque HttpOnly, SameSite=Strict caller capability.
The database stores its hash. All appointment operations bind ownership to that
server-resolved caller; the client/model cannot choose a caller ID. This is local
demo authorization, **not real-world caller identity verification**. Every
business mutation has a caller-scoped idempotency key and argument fingerprint.
Transactions, slot uniqueness, and semantic duplicate detection protect retries.

The phone simulator applies 8 kHz G.711 mu-law in both audio directions, with a
streaming low-pass filter. Seeded loss, delay and jitter affect **ingress only**;
egress has codec degradation without network perturbations. A bounded FIFO avoids
accumulating fixed latency once per packet. DTMF can arrive as real tone PCM or
explicit UI key presses. Ringing, connected, hold, transfer and hangup are simulated;
transfer reaches no real person or telephone network. Only one call/eval is active.

Evaluation shares the resident models and uses Piper speaker 20 for callers,
speaker 0 for the agent. No extra concurrent caller model is loaded. Versioned
scenarios exercise real PCM → VAD → ASR → constrained LLM → workflow → TTS.
The transport client uses the same production VoiceSession in-process, and paces
PCM in real time. It consumes complete generated audio and acknowledges complete
clauses. It is not a browser or physical audio-device benchmark. Planned barge-in
uses captured frame energy and the same cancellation control as the browser.

Scoring uses SQLite state, known scripts for WER, and the actual outgoing success
clause against the persisted row. No LLM judge. Failure injections are explicitly
identified; inference remains real. The suite records every failure rather than
adjusting expected states to make results pass. Its fixed seed controls software
perturbations; native hardware scheduling and inference timing are not bitwise
deterministic. Synthetic replay audio is saved, never live microphone audio.

## Tradeoffs and limitations

- Restricted date/time language is deliberate; arbitrary natural language dates
  require further ambiguity testing. The small LLM can still misclassify intent.
- A local capability provides record isolation, not identity assurance against
  another person controlling the same browser or operating-system account.
- Cancellation can precede a commit, but cannot undo an already committed write.
  The database and idempotency ledger remain the source of truth on retries.
- State-based safety and measured task success are separate. A low completion
  rate must be reported honestly, even if no incorrect write occurs.
- The dashboard is local and reports synthetic evaluations. No cloud upload,
  document retrieval, RAG, or generic assistant features are added.

## Findings from real validation

Real runs exposed two boundaries that unit-only testing had not covered:

- A noisy confirmation was recognized as “Yeah.” Explicit affirmative variants
  are accepted only behind the same fully-played read-back challenge.
- The compact model classified a time correction as a list request. New explicit
  date/time/doctor details now invalidate prior confirmation immediately, and
  grounded slot corrections cannot preserve an older heard proposal. A regression
  test reproduces the actual model misclassification.

The clinic ASR request now supplies only its proper-noun lexicon (Doctor Lee,
Doctor Rao, Willowmere Demo Clinic). It does not bias affirmative words, and live
mode sends an empty prompt to avoid carrying clinic context across requests.
Unknown doctor names are never automatically aliased to a supported doctor.
Failed earlier runs remain in the benchmark history; expected states are unchanged.
