"""Replaceable inference boundaries. Production never selects mocks implicitly."""
from typing import AsyncIterator, Protocol


class ASR(Protocol):
    async def transcribe(self, pcm: bytes, prompt: str = "") -> str: ...


class LLM(Protocol):
    def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...


class ToolLLM(Protocol):
    async def structured(self, messages: list[dict[str, str]], schema: dict) -> dict: ...


class TTS(Protocol):
    def stream(self, text: str, speaker: int = 0) -> AsyncIterator[tuple[int, bytes]]: ...


class VAD(Protocol):
    def reset(self) -> None: ...
    def probability(self, pcm: bytes) -> float: ...
