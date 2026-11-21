"""Calibrate benchmark speech boundaries with real fixtures, without loading the group."""
import hashlib
from pathlib import Path

import pytest

from benchmarks.phase1.run_live import fixture


@pytest.mark.parametrize("name", ["caller_short", "caller_medium", "caller_long"])
def test_live_benchmark_fixture_is_one_complete_turn_with_known_checksum(name):
    frames, last_speech, checksum = fixture(name)
    assert all(len(frame) == 1024 for frame in frames)
    assert 0 <= last_speech < len(frames) - 15  # enough actual endpoint silence follows
    path = Path(__file__).resolve().parents[1] / f"benchmarks/phase0/audio/{name}.wav"
    assert checksum == hashlib.sha256(path.read_bytes()).hexdigest()
