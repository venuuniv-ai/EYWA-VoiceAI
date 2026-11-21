"""G.711 phone codec, seeded ingress impairments, DTMF and one-call state machine."""
import asyncio
from dataclasses import dataclass
import random

import numpy as np


def ulaw_encode(samples):
    values = np.asarray(samples, dtype=np.int32)
    sign = (values < 0).astype(np.int32) * 128
    magnitude = np.minimum(np.abs(values), 32635) + 132
    exponent = np.clip(np.floor(np.log2(np.maximum(magnitude, 1))).astype(np.int32) - 7, 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 15
    return (~(sign | (exponent << 4) | mantissa) & 255).astype(np.uint8)


def ulaw_decode(data):
    value = (~np.asarray(data, dtype=np.uint8)).astype(np.int32) & 255
    magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
    magnitude -= 132
    return np.where(value & 128, -magnitude, magnitude).astype("<i2")


class PhoneCodec:
    def __init__(self):
        n = np.arange(63) - 31
        self.kernel = np.sinc(0.45 * n) * np.hamming(63)
        self.kernel /= self.kernel.sum()
        self.tail = np.zeros(62)

    def process(self, pcm: bytes, rate=16000):
        samples = np.frombuffer(pcm, dtype="<i2").astype(float)
        if rate != 16000:
            length = round(len(samples) * 16000 / rate)
            samples = np.interp(np.arange(length) * rate / 16000, np.arange(len(samples)), samples)
        combined = np.concatenate((self.tail, samples))
        filtered = np.convolve(combined, self.kernel, mode="valid")
        self.tail = combined[-62:]
        narrow = ulaw_decode(ulaw_encode(np.clip(filtered[::2], -32768, 32767).astype("<i2")))
        restored = np.interp(np.arange(len(samples)) / 2, np.arange(len(narrow)), narrow)
        return np.clip(restored, -32768, 32767).astype("<i2").tobytes()


@dataclass(frozen=True)
class PhoneSettings:
    latency_ms: int = 0
    jitter_ms: int = 0
    loss: float = 0
    seed: int = 0

    def __post_init__(self):
        if not 0 <= self.latency_ms <= 500 or not 0 <= self.jitter_ms <= 200 or not 0 <= self.loss <= 0.5:
            raise ValueError("Phone settings exceed supported limits")


class IngressLine:
    def __init__(self, settings: PhoneSettings, deliver):
        self.settings, self.deliver = settings, deliver
        self.random = random.Random(settings.seed)
        self.codec = PhoneCodec()
        self.queue = asyncio.Queue(maxsize=64)
        self.task = asyncio.create_task(self.run())

    async def put(self, pcm):
        settings = self.settings
        delay = max(0, settings.latency_ms + self.random.uniform(-settings.jitter_ms, settings.jitter_ms)) / 1000
        transformed = self.codec.process(pcm)
        if self.random.random() < settings.loss:
            transformed = b"\0" * len(transformed)
        if self.queue.full():
            raise ValueError("Phone ingress queue overflow")
        await self.queue.put((asyncio.get_running_loop().time() + delay, transformed))

    async def run(self):
        while True:
            deadline, pcm = await self.queue.get()
            await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
            await self.deliver(pcm)

    async def close(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass


class CallState:
    transitions = {"ringing": {"connected", "hangup"}, "connected": {"hold", "transfer", "hangup"},
                   "hold": {"connected", "transfer", "hangup"}, "transfer": {"hangup"}, "hangup": set()}

    def __init__(self):
        self.state = "ringing"

    def change(self, state):
        if state not in self.transitions[self.state]:
            raise ValueError("Invalid call-state transition")
        self.state = state


FREQUENCIES = (697, 770, 852, 941, 1209, 1336, 1477, 1633)
KEYPAD = ("123A", "456B", "789C", "*0#D")


def tone(digit, seconds=0.16):
    row, col = next((r, c) for r in range(4) for c in range(4) if KEYPAD[r][c] == digit)
    t = np.arange(round(16000 * seconds)) / 16000
    return ((np.sin(2 * np.pi * FREQUENCIES[row] * t) + np.sin(2 * np.pi * FREQUENCIES[col + 4] * t)) * 6500).astype("<i2").tobytes()


class DTMFDetector:
    def __init__(self):
        t = np.arange(512) / 16000
        self.bank = np.exp(-2j * np.pi * np.asarray(FREQUENCIES)[:, None] * t)
        self.last, self.count, self.emitted = None, 0, None

    def feed(self, pcm):
        samples = np.frombuffer(pcm, dtype="<i2").astype(float) / 32768
        energy = np.mean(samples ** 2)
        if energy < 0.001:
            self.last, self.count, self.emitted = None, 0, None
            return None
        powers = np.abs(self.bank @ samples) ** 2
        row, col = int(np.argmax(powers[:4])), int(np.argmax(powers[4:]))
        strong = powers[row] + powers[col + 4]
        others = np.delete(powers, [row, col + 4])
        if strong < energy * len(samples) ** 2 * 0.35 or min(powers[row], powers[col + 4]) < max(others) * 3:
            self.last, self.count = None, 0
            return None
        digit = KEYPAD[row][col]
        self.count = self.count + 1 if self.last == digit else 1
        self.last = digit
        if self.count >= 3 and self.emitted != digit:
            self.emitted = digit
            return digit
        return None
