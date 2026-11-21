import wave
from pathlib import Path

import pytest

from pipeline.vad import SileroVAD
from turn.endpoint import Endpointer, EndpointConfig

FRAME = b"\0" * 1024


def feed(endpoint, probabilities):
    return [endpoint.feed(FRAME, p) for p in probabilities]


def test_silence_and_brief_click_do_not_make_turns():
    endpoint = Endpointer()
    assert not any(e.onset or e.pcm for e in feed(endpoint, [0] * 20 + [1, 1] + [0] * 20))


def test_endpoint_delay_minimum_duration_and_reset():
    endpoint = Endpointer()
    events = feed(endpoint, [0] * 10 + [1] * 10 + [0] * 15)
    assert sum(e.onset for e in events) == 1
    ends = [e for e in events if e.pcm]
    assert len(ends) == 1
    assert ends[0].endpoint_ms - ends[0].speech_end_ms == 480
    assert len(ends[0].pcm) <= 31 * 1024
    assert not endpoint.active


def test_short_speech_is_discarded_after_onset():
    events = feed(Endpointer(), [1] * 3 + [0] * 15)
    assert any(e.onset for e in events)
    assert not any(e.pcm for e in events)


def test_silence_gap_shorter_than_threshold_does_not_split():
    events = feed(Endpointer(), [1] * 10 + [0] * 5 + [1] * 10 + [0] * 15)
    assert sum(bool(e.pcm) for e in events) == 1


def test_maximum_turn_bounds_memory_on_continuous_speech():
    endpoint = Endpointer(EndpointConfig(max_turn_ms=320))
    events = feed(endpoint, [1] * 30)
    assert sum(bool(e.pcm) for e in events) == 3
    assert all(len(e.pcm) <= 10 * 1024 for e in events if e.pcm)


@pytest.mark.parametrize("settings", [{"threshold": 0}, {"silence_ms": 0},
    {"pre_roll_ms": 32}, {"max_turn_ms": 32}])
def test_invalid_settings_rejected(settings):
    with pytest.raises(ValueError):
        EndpointConfig(**settings)


def test_wrong_frame_rejected():
    with pytest.raises(ValueError):
        Endpointer().feed(b"odd", 1)
    with pytest.raises(ValueError):
        SileroVAD().probability(b"odd")


def test_recorded_jfk_fixture_through_real_silero_and_endpoint():
    # Existing recorded human speech, not fabricated VAD probabilities.
    path = Path(__file__).resolve().parents[1] / "third_party/whisper.cpp/samples/jfk.wav"
    with wave.open(str(path)) as w:
        pcm = w.readframes(w.getnframes())
    pcm += b"\0" * 32000
    pcm += b"\0" * (-len(pcm) % 1024)
    vad, endpoint = SileroVAD(), Endpointer()
    events = [endpoint.feed(pcm[i:i+1024], vad.probability(pcm[i:i+1024]))
              for i in range(0, len(pcm), 1024)]
    assert any(e.onset for e in events)
    assert any(e.pcm for e in events)
    assert all(e.endpoint_ms - e.speech_end_ms == 480 for e in events if e.pcm)
