"""Cancellable localhost clients for resident native inference servers."""
import io
import json
import wave
from contextlib import aclosing

import httpx


class WhisperASR:
    def __init__(self, client: httpx.AsyncClient, url: str):
        self.client, self.url = client, url

    async def transcribe(self, pcm: bytes, prompt: str = "") -> str:
        audio = io.BytesIO()
        with wave.open(audio, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        response = await self.client.post(self.url + "/inference",
            data={"response_format": "json", "temperature": "0.0", "language": "en", "prompt": prompt},
            files={"file": ("utterance.wav", audio.getvalue(), "audio/wav")})
        response.raise_for_status()
        return response.json().get("text", "").strip()


class LlamaLLM:
    def __init__(self, client: httpx.AsyncClient, url: str):
        self.client, self.url = client, url
        self.timings = {}

    async def structured(self, messages, schema):
        """Native grammar-constrained JSON; schema validation still occurs at the tool boundary."""
        response = await self.client.post(self.url + "/v1/chat/completions", json={
            "messages": messages, "stream": False, "max_tokens": 192, "temperature": 0,
            "seed": 42, "cache_prompt": True,
            "response_format": {"type": "json_schema", "json_schema": {"name": "appointment_intent", "strict": True, "schema": schema}}})
        response.raise_for_status()
        payload = response.json()
        self.timings = payload.get("timings", {})
        return json.loads(payload["choices"][0]["message"]["content"])

    async def stream(self, messages: list[dict[str, str]]):
        self.timings = {}
        payload = {"messages": messages, "stream": True, "max_tokens": 96,
                   "temperature": 0, "seed": 42, "cache_prompt": True}
        async with self.client.stream("POST", self.url + "/v1/chat/completions", json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                if line[6:].strip() == "[DONE]":
                    break
                event = json.loads(line[6:])
                if event.get("timings"):
                    self.timings = event["timings"]
                for choice in event.get("choices", []):
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield piece


async def clauses(tokens):
    """Bounded clauses let TTS start while the remaining tokens are generated."""
    text = ""
    async with aclosing(tokens):
        async for token in tokens:
            text += token
            while text:
                boundary = next((i + 1 for i, ch in enumerate(text)
                                 if ch in ".!?;\n" or (i >= 100 and ch in ", ")), None)
                if boundary is None:
                    break
                clause, text = text[:boundary].strip(), text[boundary:]
                if clause:
                    yield clause
            if len(text) >= 200:
                yield text.strip()
                text = ""
        if text.strip():
            yield text.strip()
