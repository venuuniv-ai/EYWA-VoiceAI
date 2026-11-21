import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from connect.domain import Appointment, grounded_fields
from connect.store import BookingStore, Conflict, Forbidden
from connect.workflow import BookingWorkflow
from connect.telephony import CallState, DTMFDetector, PhoneCodec, PhoneSettings, IngressLine, tone, ulaw_decode, ulaw_encode
from server.app import create_app
from tests.fakes import FakeRuntime

CURRENT = date(2026, 11, 21)
FIELDS = {"date":"2026-11-23", "time":"15:00", "clinician":"Lee"}


class MockIntentLLM:
    def __init__(self, operation="book"):
        self.operation = operation
    async def structured(self, messages, schema):
        return {"operation":self.operation, "date":"", "time":"", "clinician":"", "booking_id":""}


@pytest.fixture
def store(tmp_path):
    return BookingStore(tmp_path / "bookings.sqlite3", lambda:CURRENT)


@pytest.fixture
def workflow(store):
    caller, _ = store.identity()
    return BookingWorkflow(store, caller, MockIntentLLM())


def test_writes_are_idempotent_even_under_concurrent_retries(store):
    caller, _ = store.identity()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _:store.write(caller,"request-123","book",FIELDS),range(20)))
    assert len({r["id"] for r in results}) == 1
    assert len(store.list(caller)) == 1


def test_repeated_semantic_request_and_key_conflicts(store):
    caller, _ = store.identity()
    a = store.write(caller,"request-111","book",FIELDS)
    b = store.write(caller,"request-222","book",FIELDS)
    assert a == b
    with pytest.raises(Conflict):
        store.write(caller,"request-111","book",{**FIELDS,"time":"14:00"})
    assert len(store.list(caller)) == 1


def test_record_ownership_and_unknown_callers_are_enforced(store):
    alice,_ = store.identity(); bob,_ = store.identity()
    result = store.write(alice,"alice-request","book",FIELDS)
    assert store.list(bob) == []
    with pytest.raises(Forbidden): store.get(bob,result["id"])
    with pytest.raises(Forbidden): store.write(bob,"bob-cancel","cancel",{"booking_id":result["id"]})
    with pytest.raises(Conflict): store.write(bob,"bob-booking","book",FIELDS)
    with pytest.raises(Forbidden): store.list("forged-caller")
    assert store.get(alice,result["id"])["status"] == "confirmed"


def test_superseded_idempotency_result_cannot_falsely_announce_confirmation(store):
    caller,_ = store.identity()
    result = store.write(caller,"booking-request","book",FIELDS)
    store.write(caller,"cancel-request","cancel",{"booking_id":result["id"]})
    with pytest.raises(Conflict,match="superseded"):
        store.write(caller,"booking-request","book",FIELDS)


@pytest.mark.parametrize("arguments", [{**FIELDS,"date":"2026-11-22"},{**FIELDS,"date":"2026-10-01"},
    {**FIELDS,"date":"2027-01-04"},{**FIELDS,"time":"18:00"},{**FIELDS,"caller_id":"x"}])
def test_invalid_arguments_never_create_records(store,arguments):
    caller,_ = store.identity()
    with pytest.raises(ValueError):store.write(caller,"invalid-request","book",arguments)
    assert store.list(caller) == []


async def test_commit_requires_complete_readback_and_explicit_confirmation(workflow):
    outcome = await workflow.handle("Book Monday at three PM with Doctor Lee.")
    assert outcome.state == "confirm" and not workflow.store.list(workflow.caller)
    early = await workflow.handle("Yes.")
    assert early.state == "confirm" and not early.success
    workflow.played(early.challenge)
    outcome = await workflow.handle("Yes.")
    assert outcome.state == "report" and outcome.success
    assert workflow.store.get(workflow.caller,outcome.booking["id"])["status"] == "confirmed"
    await workflow.handle("Yes.")
    assert len(workflow.store.list(workflow.caller)) == 1


async def test_change_of_mind_invalidates_old_playback_challenge(workflow):
    old = await workflow.handle("Book Monday at three PM with Doctor Lee.")
    new = await workflow.handle("Actually book Thursday at two PM with Doctor Rao.")
    workflow.played(old.challenge)
    assert not workflow.heard
    workflow.played(new.challenge)
    outcome = await workflow.handle("Yes.")
    assert outcome.booking["date"] == "2026-11-26" and outcome.booking["time"] == "14:00"


async def test_interrupted_readback_no_and_ambiguity_never_commit(workflow):
    proposal = await workflow.handle("Book Monday at three PM with Doctor Lee.")
    workflow.interrupted()
    workflow.played(proposal.challenge)
    assert not workflow.heard
    await workflow.handle("Yes.")
    assert not workflow.store.list(workflow.caller)
    await workflow.handle("No.")
    await workflow.handle("Book Monday or Tuesday at three PM with Doctor Lee.")
    await workflow.handle("Yes.")
    assert not workflow.store.list(workflow.caller)


async def test_failed_transaction_is_not_announced_successful(workflow,monkeypatch):
    proposal=await workflow.handle("Book Monday at three PM with Doctor Lee.")
    workflow.played(proposal.challenge)
    def fail(*args,**kwargs):raise sqlite3.OperationalError("injected database failure")
    monkeypatch.setattr(workflow.store,"write",fail)
    outcome=await workflow.handle("Yes.")
    assert not outcome.success and "did not complete" in outcome.text
    assert workflow.store.list(workflow.caller) == []


@pytest.mark.parametrize('affirmation',['Yeah.','Yep.','Yeah please.'])
async def test_asr_confirmation_variants_still_require_heard_readback(workflow,affirmation):
    await workflow.handle('Book Monday at three PM with Doctor Lee.')
    early=await workflow.handle(affirmation)
    assert not early.success and workflow.store.list(workflow.caller)==[]
    workflow.played(early.challenge)
    result=await workflow.handle(affirmation)
    assert result.success and len(workflow.store.list(workflow.caller))==1


async def test_model_misclassification_cannot_preserve_stale_confirmed_slot(workflow):
    first=await workflow.handle('Book Tuesday at ten AM with Doctor Rao.')
    workflow.played(first.challenge)
    workflow.llm.operation='list'  # Reproduces the actual small-model failure.
    correction=await workflow.handle('Actually booked Tuesday at two PM with Doctor Rao.')
    assert correction.state=='confirm' and not workflow.heard
    early=await workflow.handle('Yes.')
    assert not early.success and workflow.store.list(workflow.caller)==[]
    workflow.played(early.challenge)
    result=await workflow.handle('Yes.')
    assert result.booking['time']=='14:00'


async def test_intervening_query_cannot_confirm_an_older_proposal(workflow):
    first=await workflow.handle('Book Monday at three PM with Doctor Lee.')
    workflow.played(first.challenge)
    workflow.llm.operation='list'
    await workflow.handle('List my appointments.')
    assert not workflow.heard
    reply=await workflow.handle('Yes.')
    assert not reply.success and workflow.store.list(workflow.caller)==[]
    assert reply.challenge and reply.challenge!=first.challenge


def test_date_and_time_grounding_refuses_ambiguity():
    assert grounded_fields("Monday at three PM with Doctor Lee",CURRENT) == {**FIELDS,"booking_id":""}
    assert grounded_fields("Monday or Tuesday at three",CURRENT)["date"] == ""
    assert grounded_fields("Monday at three",CURRENT)["time"] == ""


def test_g711_known_vectors_and_roundtrip():
    assert ulaw_encode(np.array([0,-1,32124,-32124],dtype=np.int16)).tolist() == [255,127,128,0]
    assert ulaw_decode(np.array([255,127,128,0],dtype=np.uint8)).tolist() == [0,0,32124,-32124]
    audio=np.sin(np.arange(16000)*2*np.pi*440/16000)*10000
    out=PhoneCodec().process(audio.astype("<i2").tobytes())
    assert len(out)==32000 and np.max(np.abs(np.frombuffer(out,dtype="<i2")))>5000


@pytest.mark.parametrize("digit",list("123456789*0#"))
def test_dtmf_real_tones_emit_once_per_press(digit):
    detector=DTMFDetector();pcm=tone(digit)
    result=[detector.feed(pcm[i:i+1024]) for i in range(0,len(pcm)-1023,1024)]
    assert [v for v in result if v] == [digit]
    assert detector.feed(b"\0"*1024) is None


def test_call_state_and_phone_settings_reject_invalid_changes():
    call=CallState();call.change("connected");call.change("hold");call.change("connected");call.change("transfer");call.change("hangup")
    with pytest.raises(ValueError):call.change("connected")
    with pytest.raises(ValueError):PhoneSettings(loss=2)


async def test_ingress_delay_is_not_accumulated_once_per_packet():
    received=[]
    async def deliver(pcm):received.append(asyncio.get_running_loop().time())
    start=asyncio.get_running_loop().time()
    line=IngressLine(PhoneSettings(latency_ms=40),deliver)
    try:
        for _ in range(5):await line.put(b"\0"*1024)
        async with asyncio.timeout(1):
            while len(received)<5:await asyncio.sleep(.005)
        assert received[-1]-start<.15
    finally:await line.close()


def test_http_caller_capability_is_required_and_isolates_records(tmp_path):
    with TestClient(create_app(FakeRuntime,data_dir=tmp_path)) as client:
        assert client.get('/api/bookings').status_code == 401
        client.get('/api/session')
        token=client.cookies.get('eywa_caller')
        assert 'HttpOnly' in client.get('/api/session').headers.get('set-cookie','HttpOnly')
        store=client.app.state.bookings
        owner=store.resolve(token)
        # Use store's real current date for the HTTP test.
        fields=grounded_fields("Monday at three PM with Doctor Lee",store.current_date())
        store.write(owner,'http-booking','book',{k:fields[k] for k in ('date','time','clinician')})
        assert len(client.get('/api/bookings').json()['bookings']) == 1
        client.cookies.clear();client.get('/api/session')
        assert client.get('/api/bookings').json()['bookings'] == []
