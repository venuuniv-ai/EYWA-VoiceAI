# 0003: Piper voice `en_US-libritts_r-medium` for both agent and synthetic callers

Date: 2026-11-03. Status: accepted.

## Context
CLAUDE.md requires checking each voice's license. Eval needs caller voices that are different from the agent's voice.

Model cards checked at piper-voices commit `e7229f7`:

| Voice | Dataset license |
|---|---|
| lessac medium | Blizzard 2013 Lessac license (research / non-commercial terms) |
| ryan medium | CC BY-NC-SA 4.0 |
| hfc_female / hfc_male | CC BY-NC-SA 4.0 |
| amy medium | "See URL" (Mycroft mimic3; unclear) |
| **libritts_r medium** | **CC BY 4.0**, 904 speakers |

## Decision
Use `en_US-libritts_r-medium`. The agent uses speaker index 0 and synthetic callers use other indices (speaker 20 in Phase 0). One 79 MB model file covers both roles. Attribution for LibriTTS-R (CC BY 4.0) goes in the README.

## Licensing note
The `piper-tts` 1.8.0 Python runtime (OHF-Voice/piper1-gpl) is **GPL-3.0**, and it bundles espeak-ng (GPL-3.0) for phonemization. If EYWA is distributed together with it, the combined work falls under GPL terms. That's acceptable for an open-source portfolio repo; the license choice will be recorded in Phase 5.

## Alternatives
- Single-speaker NC voices (lessac, ryan): slightly cleaner single-voice quality, but non-commercial terms and a second model would be needed for callers.
- Callers spoken by a separate TTS engine: more distinct voices, but more RAM and disk. Revisit in Phase 3 if a scenario needs it.

## Tradeoffs
Agent and callers share the same acoustic model, so they may sound more alike than real callers would. That could make ASR look better than it really is. Eval should also include real recorded speech fixtures.
