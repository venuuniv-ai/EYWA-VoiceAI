"""Download EYWA model files at pinned revisions and verify SHA-256 checksums.

Usage:  python scripts/download_models.py [--only NAME ...] [--list]

Every file is pinned to an immutable commit and checked against a SHA-256
hash. A file that fails verification is deleted and the script exits non-zero.
Already-present files that verify are skipped, so the script is safe to re-run.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

HF = "https://huggingface.co"
QWEN_REV = "91cad51170dc346986eccefdc2dd33a9da36ead9"
QWEN_COMPACT_REV = "9217f5db79a29953eb74d5343926648285ec7e67"
WHISPER_REV = "5359861c739e955e79d9a303bcbc70fb988958b1"
PIPER_REV = "e7229f7d33a10e145e109b886d661984201ac3c4"
SILERO_TAG = "v6.2.3"


@dataclass(frozen=True)
class ModelFile:
    name: str      # short id used with --only
    dest: str      # path under models/
    url: str
    sha256: str
    size: int | None = None


PIPER_BASE = f"{HF}/rhasspy/piper-voices/resolve/{PIPER_REV}/en/en_US/libritts_r/medium"

FILES: list[ModelFile] = [
    ModelFile(
        "qwen-compact", "llm/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        f"{HF}/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/{QWEN_COMPACT_REV}/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db", 491400032),
    ModelFile(
        "qwen", "llm/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        f"{HF}/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/{QWEN_REV}/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e", 1117320736),
    ModelFile(
        "whisper-base", "asr/ggml-base.en.bin",
        f"{HF}/ggerganov/whisper.cpp/resolve/{WHISPER_REV}/ggml-base.en.bin",
        "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002", 147964211),
    ModelFile(
        "whisper-small", "asr/ggml-small.en.bin",
        f"{HF}/ggerganov/whisper.cpp/resolve/{WHISPER_REV}/ggml-small.en.bin",
        "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d", 487614201),
    ModelFile(
        "piper", "tts/en_US-libritts_r-medium.onnx",
        f"{PIPER_BASE}/en_US-libritts_r-medium.onnx",
        "10bb85e071d616fcf4071f369f1799d0491492ab3c5d552ec19fb548fac13195", 78580914),
    ModelFile(
        "piper", "tts/en_US-libritts_r-medium.onnx.json",
        f"{PIPER_BASE}/en_US-libritts_r-medium.onnx.json",
        "b471dc60d2d8335e819c393d196d6fbf792817f40051257b269878505bc9afb3"),
    ModelFile(
        "piper", "tts/en_US-libritts_r-medium.MODEL_CARD",
        f"{PIPER_BASE}/MODEL_CARD",
        "0ccde6927e5bb4d743f4ea39618a9387ba18cca3351220a8a9cfdbc68b30fcb9"),
    ModelFile(
        "silero", "vad/silero_vad.onnx",
        f"https://raw.githubusercontent.com/snakers4/silero-vad/{SILERO_TAG}/src/silero_vad/data/silero_vad.onnx",
        "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3", 2327524),
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(mf: ModelFile) -> bool:
    dest = MODELS / mf.dest
    if dest.exists() and sha256_of(dest) == mf.sha256:
        print(f"ok (cached)  {mf.dest}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"downloading  {mf.dest} ...", flush=True)
    with urllib.request.urlopen(mf.url) as r, tmp.open("wb") as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)
    got = sha256_of(tmp)
    if got != mf.sha256:
        tmp.unlink()
        print(f"CHECKSUM MISMATCH {mf.dest}: expected {mf.sha256}, got {got}", file=sys.stderr)
        return False
    tmp.rename(dest)
    print(f"ok (verified) {mf.dest}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="subset of names: " + ", ".join(sorted({f.name for f in FILES})))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    files = [f for f in FILES if not args.only or f.name in args.only]
    if args.list:
        for f in files:
            print(f"{f.name:14} {f.dest:45} {f.sha256}")
        return 0
    ok = all([fetch(f) for f in files])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
