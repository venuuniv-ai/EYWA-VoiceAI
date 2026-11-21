"""Explicit test doubles only. No production import path selects these."""
import asyncio
from contextlib import nullcontext


class FakeSpan:
    def set_attribute(self, *args):
        pass


class FakeTracing:
    def span(self, *args):
        return nullcontext(FakeSpan())


class MockVAD:
    def reset(self):
        pass

    def probability(self, pcm):
        return 1.0 if any(pcm) else 0.0


class MockASR:
    def __init__(self):
        self.started, self.cancelled = asyncio.Event(), asyncio.Event()
        self.block = False

    async def transcribe(self, pcm, prompt=""):
        self.started.set()
        try:
            if self.block:
                await asyncio.Event().wait()
            return "Hello EYWA"
        finally:
            self.cancelled.set()


class FakeLLM:
    def __init__(self):
        self.started, self.cancelled = asyncio.Event(), asyncio.Event()
        self.block = False
        self.messages = []

    async def stream(self, messages):
        self.messages = messages
        self.started.set()
        try:
            yield "Hello."
            if self.block:
                await asyncio.Event().wait()
            yield " How can I help?"
        finally:
            self.cancelled.set()


class MockTTS:
    def __init__(self):
        self.started, self.cancelled = asyncio.Event(), asyncio.Event()
        self.block = False

    async def stream(self, text, speaker=0):
        self.started.set()
        try:
            if self.block:
                await asyncio.Event().wait()
            yield 16000, b"\0\0" * 1600
        finally:
            self.cancelled.set()

    async def close(self):
        pass


class FakeRuntime:
    mode = "MOCK — test-only ASR / LLM / TTS / VAD"

    def __init__(self):
        self.vad, self.asr, self.llm, self.tts = MockVAD(), MockASR(), FakeLLM(), MockTTS()
        self.tracing = FakeTracing()

    async def start(self):
        pass

    async def close(self):
        pass
