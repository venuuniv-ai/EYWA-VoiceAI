"""Phase 0 tests: admission check, checksum-verified downloads, WER, Silero VAD wrapper."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "benchmarks/phase0")]

import download_models  # noqa: E402
from pipeline.admission import MB, InsufficientMemory, check_admission, decide  # noqa: E402
from run_phase0 import wer  # noqa: E402
from workers import SileroVAD, read_wav_16k, vad_probs  # noqa: E402


# ---------------------------------------------------------------- admission
def test_admission_admits_when_headroom_remains():
    d = check_admission("m", 1000 * MB, headroom_bytes=500 * MB, available_fn=lambda: 1500 * MB)
    assert d.admitted and d.reason.startswith("admitted")


def test_admission_refuses_with_clear_reason():
    with pytest.raises(InsufficientMemory) as e:
        check_admission("qwen", 1000 * MB, headroom_bytes=512 * MB, available_fn=lambda: 1200 * MB)
    msg = str(e.value)
    assert "REFUSED" in msg and "qwen" in msg and "1000 MB" in msg and "1200 MB" in msg and "512 MB" in msg


def test_admission_boundary_is_inclusive():
    assert decide("m", 100, headroom_bytes=50, available_fn=lambda: 150).admitted
    assert not decide("m", 100, headroom_bytes=50, available_fn=lambda: 149).admitted


def test_admission_rejects_negative_inputs():
    with pytest.raises(ValueError):
        decide("m", -1)


def test_admission_uses_real_system_memory_by_default():
    assert decide("m", 0).available_bytes > 0


# ---------------------------------------------------------------- downloads
def _local_file(tmp_path: Path, content: bytes) -> tuple[str, str]:
    src = tmp_path / "src.bin"
    src.write_bytes(content)
    return src.as_uri(), hashlib.sha256(content).hexdigest()


def test_download_verifies_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(download_models, "MODELS", tmp_path / "models")
    url, sha = _local_file(tmp_path, b"model-bytes")
    mf = download_models.ModelFile("x", "a/model.bin", url, sha)
    assert download_models.fetch(mf)
    assert (tmp_path / "models/a/model.bin").read_bytes() == b"model-bytes"
    assert download_models.fetch(mf)  # cached path re-verifies


def test_download_rejects_bad_checksum_and_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(download_models, "MODELS", tmp_path / "models")
    url, _ = _local_file(tmp_path, b"tampered")
    mf = download_models.ModelFile("x", "a/model.bin", url, "0" * 64)
    assert not download_models.fetch(mf)
    assert not list((tmp_path / "models/a").iterdir())


def test_all_pinned_files_have_sha256_and_immutable_revisions():
    for f in download_models.FILES:
        assert len(f.sha256) == 64 and int(f.sha256, 16) >= 0
        assert "/main/" not in f.url and "/master/" not in f.url, f.url


# ---------------------------------------------------------------- WER
def test_wer_basic():
    assert wer("a b c d", "a b c d") == 0
    assert wer("a b c d", "a x c") == pytest.approx(0.5)  # 1 sub + 1 del
    assert wer("a b", "a b c") == pytest.approx(0.5)  # 1 insertion


def test_wer_normalization_is_optional():
    ref, hyp = "see Doctor Lee at three", "See Dr. Lee at 3."
    assert wer(ref, hyp) == 0
    assert wer(ref, hyp, normalize=False) == pytest.approx(0.4)


# ---------------------------------------------------------------- Silero VAD (real model)
@pytest.fixture(scope="module")
def vad():
    return SileroVAD()


def test_vad_silence_is_not_speech(vad):
    vad.reset()
    probs, _ = vad_probs(vad, np.zeros(16000, dtype=np.float32))
    assert max(probs) < 0.5


def test_vad_detects_speech_in_fixture(vad):
    vad.reset()
    audio = read_wav_16k(ROOT / "third_party/whisper.cpp/samples/jfk.wav")
    probs, _ = vad_probs(vad, audio)
    assert sum(p > 0.5 for p in probs) / len(probs) > 0.3


def test_vad_reset_makes_runs_deterministic(vad):
    audio = read_wav_16k(ROOT / "third_party/whisper.cpp/samples/jfk.wav")[:16000 * 3]
    vad.reset()
    a, _ = vad_probs(vad, audio)
    vad.reset()
    b, _ = vad_probs(vad, audio)
    assert np.allclose(a, b)
