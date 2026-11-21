"""Known synthetic fixtures only. Never records a user's microphone."""
from contextlib import aclosing
import math

import numpy as np


async def caller_audio(tts, text, speed=1.0, snr_db=None, seed=0):
    chunks = []
    async with aclosing(tts.stream(text, speaker=20)) as stream:
        async for rate, pcm in stream:
            chunks.append(np.frombuffer(pcm, dtype="<i2").astype(float))
    if not chunks:
        raise ValueError("Caller voice produced no audio")
    values = np.concatenate(chunks)
    length = max(1, round(len(values) * 16000 / rate / speed))
    values = np.interp(np.arange(length) * rate * speed / 16000, np.arange(len(values)), values)
    if snr_db is not None:
        rng = np.random.default_rng(seed)
        rms = math.sqrt(float(np.mean(values ** 2)))
        values += rng.normal(0, rms / (10 ** (snr_db / 20)), len(values))
    return np.clip(values, -32768, 32767).astype("<i2").tobytes()
