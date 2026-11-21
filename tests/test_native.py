import asyncio
import json
from contextlib import aclosing

import httpx
import pytest

from pipeline.native import LlamaLLM, WhisperASR, clauses
from pipeline.tts import PiperTTS
from pipeline.admission import InsufficientMemory
from server.runtime import Runtime


async def test_llama_stream_closes_http_response_on_cancel():
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            await asyncio.Event().wait()
        async def aclose(self):
            self.closed = True
    stream = Stream()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream))) as client:
        async def consume():
            async with aclosing(LlamaLLM(client, "http://local").stream([])) as tokens:
                async for _ in tokens:
                    got.set()
        got = asyncio.Event()
        task = asyncio.create_task(consume())
        await got.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed


async def test_whisper_upload_is_wav_and_inference_errors_are_not_silently_replaced():
    def handler(request):
        assert b"RIFF" in request.content and b"WAVE" in request.content
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await WhisperASR(client, "http://local").transcribe(b"\0" * 32000)


async def test_clause_generator_is_bounded_and_closes_upstream():
    closed = False
    async def tokens():
        nonlocal closed
        try:
            yield "Hello. Next, "
            yield "x" * 250
        finally:
            closed = True
    result = [c async for c in clauses(tokens())]
    assert result[0] == "Hello." and closed


async def test_piper_cancellation_terminates_owned_process(monkeypatch):
    # IPC/termination is real; synthesis is a deliberately hanging test worker.
    tts = PiperTTS()
    async def start():
        import sys
        tts.proc = await asyncio.create_subprocess_exec(sys.executable, "-c",
            "import sys,time; sys.stdin.readline(); time.sleep(60)",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    monkeypatch.setattr(tts, "start", start)
    async def consume():
        async with aclosing(tts.stream("private test text")) as stream:
            async for _ in stream:
                pass
    task = asyncio.create_task(consume())
    async with asyncio.timeout(2):
        while tts.proc is None:
            await asyncio.sleep(0.001)
    proc = tts.proc
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.returncode is not None and tts.proc is None


async def test_low_memory_startup_does_not_launch_any_models(monkeypatch):
    import server.runtime as module
    def refuse(*args, **kwargs):
        raise InsufficientMemory("REFUSED: test memory pressure")
    monkeypatch.setattr(module, "check_admission", refuse)
    runtime = Runtime()
    try:
        with pytest.raises(InsufficientMemory, match="REFUSED"):
            await runtime.start()
        assert runtime.processes == [] and runtime.tts.proc is None
    finally:
        await runtime.close()


def test_local_traces_do_not_record_sensitive_exception_data(tmp_path):
    from server.tracing import LocalTracing
    tracing = LocalTracing(tmp_path)
    with pytest.raises(ValueError):
        with tracing.span("asr", 1):
            raise ValueError("private phone 212-555-0199")
    tracing.close()
    text = next(tmp_path.glob("spans-*.jsonl")).read_text()
    assert "212-555" not in text and json.loads(text)["name"] == "asr"


async def test_llama_reports_native_token_counts_instead_of_counting_sse_messages():
    data = ('data: {"choices":[{"delta":{"content":"Hello there."}}]}\n\n'
            'data: {"choices":[],"timings":{"predicted_n":4,"predicted_per_second":50.0}}\n\n'
            'data: [DONE]\n\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=data))) as client:
        llm = LlamaLLM(client, "http://local")
        assert [s async for s in llm.stream([])] == ["Hello there."]
        assert llm.timings["predicted_n"] == 4


async def test_partial_startup_refusal_reaps_already_loaded_server(monkeypatch):
    import server.runtime as module
    created = []
    class OwnedProcess:
        pid = 123456789
        returncode = None
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            self.returncode = 0
    def spawn(*args, **kwargs):
        proc = OwnedProcess()
        created.append(proc)
        return proc
    def admit(model, *args, **kwargs):
        if "ggml-base" in model:
            raise InsufficientMemory("REFUSED: memory changed after LLM load")
    signalled = []
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module, "check_admission", admit)
    monkeypatch.setattr(module, "signal_owned", lambda *args, **kwargs: signalled.append(args))
    runtime = Runtime()
    async def healthy(*args, **kwargs):
        return httpx.Response(200)
    monkeypatch.setattr(runtime.client, "get", healthy)
    try:
        with pytest.raises(InsufficientMemory, match="changed"):
            await runtime.start()
    finally:
        await runtime.close()
    assert len(created) == 1 and created[0].returncode == 0
    assert signalled == [(created[0].pid, False)]


async def test_speculative_piper_reload_is_single_and_cancelled_on_close(monkeypatch):
    tts = PiperTTS()
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0
    async def start():
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(tts, "start", start)
    tts.prepare()
    tts.prepare()
    await entered.wait()
    await tts.close()
    assert calls == 1 and cancelled.is_set() and tts.warming is None


async def test_failed_speculative_reload_does_not_block_requested_synthesis(monkeypatch):
    tts = PiperTTS()
    async def fail():
        raise InsufficientMemory("REFUSED: test reserve")
    monkeypatch.setattr(tts, "start", fail)
    tts.prepare()
    task = tts.warming
    with pytest.raises(InsufficientMemory):
        await task
    await asyncio.sleep(0)
    assert tts.warming is None
    with pytest.raises(InsufficientMemory):
        await anext(tts.stream("Hello"))
    await tts.close()
