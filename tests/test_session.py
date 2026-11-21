import asyncio

import pytest

from server.session import Reply, VoiceSession
from tests.fakes import FakeRuntime


@pytest.fixture
def session():
    events = []

    async def send(event):
        events.append(event)
    s = VoiceSession(FakeRuntime(), send)
    s.events = events
    return s


async def begin(session):
    session.generation = 1
    session.reply = Reply(1)
    session.task = asyncio.create_task(session.respond(b"\0" * 32000, 1, 480))


async def wait_for_event(session, kind):
    async with asyncio.timeout(2):
        while not any(e["type"] == kind for e in session.events):
            await asyncio.sleep(0.001)


@pytest.mark.parametrize("stage", ["asr", "llm", "tts"])
async def test_interruption_cancels_each_inflight_stage_and_discards_late_output(session, stage):
    component = getattr(session.runtime, stage)
    component.block = True
    await begin(session)
    await asyncio.wait_for(component.started.wait(), 2)
    await session.interrupt()
    assert component.cancelled.is_set()
    assert session.task is None and session.generation == 2
    assert not any(e["type"] == "response_done" for e in session.events)
    assert not any(m["role"] == "assistant" for m in session.history)
    assert session.events[-1]["type"] == "interrupted"


async def test_unplayed_text_is_never_retained_and_acknowledgements_are_ordered(session):
    await begin(session)
    await session.task
    assert len(session.reply.clauses) == 2
    await session.acknowledge(999, 0)  # stale/forged generation
    await session.acknowledge(1, 1)  # out of order
    assert not session.reply.played
    await session.acknowledge(1, 0)
    await session.acknowledge(1, 0)  # duplicate
    await session.interrupt()
    assert session.history[-1] == {"role": "assistant", "content": "Hello."}
    assert "How can I help?" not in str(session.history)
    await session.acknowledge(1, 1)
    assert session.history[-1]["content"] == "Hello."


async def test_disconnect_cancels_and_erases_session_state(session):
    session.runtime.llm.block = True
    await begin(session)
    await session.runtime.llm.started.wait()
    await session.close()
    assert session.runtime.llm.cancelled.is_set()
    assert not session.history and session.reply is None
    assert not session.endpoint.frames


async def test_generation_failure_cancels_sibling_without_leaking_exception_text(session):
    async def fail(messages):
        raise ValueError("private phone number 212-555-0199")
        yield
    session.runtime.llm.stream = fail
    await begin(session)
    await session.task
    assert session.events[-1]["type"] == "error"
    assert "212-555" not in str(session.events)
    assert not any(e["type"] == "response_done" for e in session.events)


async def test_real_streams_close_when_consumer_cancelled(session):
    session.runtime.llm.block = session.runtime.tts.block = True
    await begin(session)
    await session.runtime.tts.started.wait()
    await session.interrupt()
    assert session.runtime.llm.cancelled.is_set()
    assert session.runtime.tts.cancelled.is_set()


async def test_complete_turn_has_stage_metrics_but_no_generated_history(session):
    await begin(session)
    await session.task
    event = session.events[-1]
    assert event["type"] == "response_done"
    assert event["metrics"]["server_voice_to_voice_ms"] >= 480
    assert all(key in event["metrics"] for key in ("asr_ms", "asr_rtf", "llm_ttft_ms", "tts_first_chunk_ms"))
    assert [m["role"] for m in session.history] == ["user"]


async def test_backpressure_does_not_deadlock_interrupt(session):
    async def many(messages):
        for _ in range(100):
            yield "Hello."
    session.runtime.llm.stream = many
    await begin(session)
    async with asyncio.timeout(2):
        while len(session.reply.clauses) < 2:
            await asyncio.sleep(0.001)
    assert not session.task.done()
    await asyncio.wait_for(session.interrupt(), 1)
    assert session.task is None


async def test_empty_synthesis_cannot_claim_a_playable_clause(session):
    async def empty(text):
        if False:
            yield
    session.runtime.tts.stream = empty
    await begin(session)
    await session.task
    assert session.events[-1]["type"] == "error"
    assert not any(e["type"] == "clause_end" for e in session.events)
    assert not session.reply.clauses


def test_history_is_bounded_for_native_context_and_starts_with_user(session):
    session.history = [{"role": role, "content": "x" * 2000}
        for role in ["user", "assistant"] * 12]
    session.prune_history()
    assert sum(len(m["content"]) for m in session.history) <= 6000
    assert session.history[0]["role"] == "user"


async def test_memory_refusal_during_synthesis_is_reported_clearly(session):
    from pipeline.admission import InsufficientMemory
    async def refuse(text):
        raise InsufficientMemory("REFUSED: Piper needs 223 MB; must keep 512 MB free")
        yield
    session.runtime.tts.stream = refuse
    await begin(session)
    await session.task
    event = session.events[-1]
    assert event["error_class"] == "InsufficientMemory"
    assert "REFUSED" in event["message"] and "512 MB" in event["message"]
