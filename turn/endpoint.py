"""Sample-clock endpointing, independent of inference and wall-clock scheduling."""
from collections import deque
from dataclasses import dataclass

FRAME_MS = 32
FRAME_BYTES = 1024


@dataclass(frozen=True)
class EndpointConfig:
    threshold: float = 0.5
    onset_ms: int = 96
    min_speech_ms: int = 160
    silence_ms: int = 480
    max_turn_ms: int = 15000
    pre_roll_ms: int = 192

    def __post_init__(self):
        if not 0 < self.threshold < 1:
            raise ValueError("threshold must be between zero and one")
        if min(self.onset_ms, self.min_speech_ms, self.silence_ms, self.max_turn_ms) <= 0:
            raise ValueError("durations must be positive")
        if self.pre_roll_ms < self.onset_ms or self.max_turn_ms < self.min_speech_ms:
            raise ValueError("pre-roll must cover onset; max turn must cover minimum speech")


@dataclass
class EndpointEvent:
    onset: bool = False
    pcm: bytes | None = None
    speech_end_ms: int = 0
    endpoint_ms: int = 0


class Endpointer:
    def __init__(self, config: EndpointConfig = EndpointConfig()):
        self.config = config
        self.clock_ms = 0
        self.reset()

    def reset(self):
        self.pre_roll = deque(maxlen=(self.config.pre_roll_ms + 31) // 32)
        self.frames: list[bytes] = []
        self.active = False
        self.consecutive_ms = self.speech_ms = self.silence_ms = 0
        self.last_speech_ms = self.clock_ms

    def feed(self, pcm: bytes, probability: float) -> EndpointEvent:
        if len(pcm) != FRAME_BYTES:
            raise ValueError("expected a 32 ms PCM16 frame")
        self.clock_ms += FRAME_MS
        speech = probability >= self.config.threshold
        event = EndpointEvent()
        if not self.active:
            self.pre_roll.append(pcm)
            self.consecutive_ms = self.consecutive_ms + FRAME_MS if speech else 0
            if self.consecutive_ms < self.config.onset_ms:
                return event
            self.active = event.onset = True
            self.frames = list(self.pre_roll)
            self.speech_ms = self.consecutive_ms
            self.last_speech_ms = self.clock_ms
            return event
        self.frames.append(pcm)
        if speech:
            self.speech_ms += FRAME_MS
            self.silence_ms = 0
            self.last_speech_ms = self.clock_ms
        else:
            self.silence_ms += FRAME_MS
        if (self.silence_ms >= self.config.silence_ms
                or len(self.frames) * FRAME_MS >= self.config.max_turn_ms):
            if self.speech_ms >= self.config.min_speech_ms:
                event.pcm = b"".join(self.frames)
                event.speech_end_ms = self.last_speech_ms
                event.endpoint_ms = self.clock_ms
            self.reset()
        return event
