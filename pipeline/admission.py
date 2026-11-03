"""Memory admission check: refuse to load a model when free memory is too low.

Usage:
    check_admission("qwen2.5-1.5b-q4", required_bytes=1_400 * MB)   # raises InsufficientMemory

`required_bytes` should come from measured numbers (benchmarks/phase0), not guesses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import psutil

MB = 1024 * 1024
DEFAULT_HEADROOM_BYTES = 512 * MB  # memory that must stay free *after* the model loads


class InsufficientMemory(RuntimeError):
    pass


@dataclass(frozen=True)
class AdmissionDecision:
    model: str
    required_bytes: int
    available_bytes: int
    headroom_bytes: int

    @property
    def admitted(self) -> bool:
        return self.available_bytes - self.required_bytes >= self.headroom_bytes

    @property
    def reason(self) -> str:
        verdict = "admitted" if self.admitted else "REFUSED"
        return (f"{verdict}: loading '{self.model}' needs {self.required_bytes / MB:.0f} MB; "
                f"{self.available_bytes / MB:.0f} MB available; "
                f"must keep {self.headroom_bytes / MB:.0f} MB free afterwards")


def _available() -> int:
    return psutil.virtual_memory().available


def decide(model: str, required_bytes: int, headroom_bytes: int = DEFAULT_HEADROOM_BYTES,
           available_fn: Callable[[], int] = _available) -> AdmissionDecision:
    if required_bytes < 0 or headroom_bytes < 0:
        raise ValueError("required_bytes and headroom_bytes must be non-negative")
    return AdmissionDecision(model, required_bytes, available_fn(), headroom_bytes)


def check_admission(model: str, required_bytes: int, headroom_bytes: int = DEFAULT_HEADROOM_BYTES,
                    available_fn: Callable[[], int] = _available) -> AdmissionDecision:
    """Return the decision if admitted; raise InsufficientMemory with a clear reason otherwise."""
    d = decide(model, required_bytes, headroom_bytes, available_fn)
    if not d.admitted:
        raise InsufficientMemory(d.reason)
    return d
