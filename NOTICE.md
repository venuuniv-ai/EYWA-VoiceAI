# Licenses and attribution

EYWA source code is offered under GPL-3.0-or-later, consistent with the Piper
runtime choice recorded in ADR 0003. See LICENSE for the GPLv3 text. Models and
third-party dependencies retain their respective licenses; their binaries and
weights are downloaded separately and are not committed to this repository.

- **Piper / espeak-ng:** the pinned `piper-tts` runtime is GPL-3.0. Upstream:
  https://github.com/OHF-Voice/piper1-gpl .
- **Piper voice `en_US-libritts_r-medium`:** the pinned model card identifies the
  LibriTTS-R dataset as **CC BY 4.0**, https://www.openslr.org/141/ . Model card:
  https://huggingface.co/rhasspy/piper-voices/blob/e7229f7d33a10e145e109b886d661984201ac3c4/en/en_US/libritts_r/medium/MODEL_CARD .
- **Dataset citation:** Yuma Koizumi, Heiga Zen, Shigeki Karita, Yifan Ding, Kohei
  Yatabe, Nobuyuki Morioka, Michiel Bacchiani, Yu Zhang, Wei Han, and Ankur Bapna,
  “LibriTTS-R: A Restored Multi-Speaker Text-to-Speech Corpus,” 2023.
  EYWA uses the derived voice model to generate agent and synthetic-caller speech;
  it resamples, optionally adds noise, applies a phone codec, and mixes generated
  PCM in the labelled demo. No endorsement by the dataset authors is implied.
- **Qwen models:** official Qwen2.5 0.5B and 1.5B Instruct GGUF artifacts,
  Apache-2.0 as identified by their official model repositories. Exact revisions
  and checksums appear in `scripts/download_models.py`.
- **whisper.cpp, llama.cpp and Silero:** consult their pinned upstream licenses
  before redistributing their code or model artifacts. EYWA's setup retains the
  native repositories locally under the ignored `third_party/` directory.

The committed synthetic demo is a fixture demonstration with real inference and
generated speech. It contains no user microphone recording or real patient data.
