import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.app import create_app
from tests.fakes import FakeRuntime
from tests.test_connect import MockIntentLLM


def receive_turn(ws):
    events=[]
    while True:
        event=ws.receive_json();events.append(event)
        if event['type']=='response_done':return events


def send_speech(ws):
    for frame in [b'\1\0'*512]*10+[b'\0'*1024]*15:ws.send_bytes(frame)


def test_connect_requires_caller_cookie_and_full_readback_before_dtmf_commit(tmp_path):
    runtime=FakeRuntime();runtime.llm=MockIntentLLM()
    async def transcript(pcm,prompt=""):return 'Book Monday at three PM with Doctor Lee.'
    runtime.asr.transcribe=transcript
    with TestClient(create_app(lambda:runtime,data_dir=tmp_path)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws?mode=connect'):pass
        client.get('/api/session')
        with client.websocket_connect('/ws?mode=connect') as ws:
            assert ws.receive_json()['type']=='ready'
            send_speech(ws)
            first=receive_turn(ws)
            assert client.get('/api/bookings').json()['bookings']==[]
            ws.send_json({'type':'dtmf','digit':'1'})
            second=receive_turn(ws)
            assert client.get('/api/bookings').json()['bookings']==[]
            clause=next(e for e in second if e['type']=='clause_end')
            ws.send_json({'type':'played','turn_id':clause['turn_id'],'clause_id':clause['clause_id']})
            ws.send_json({'type':'dtmf','digit':'1'})
            third=receive_turn(ws)
            assert any(e['type']=='booking_state' and e['success'] for e in third)
            assert len(client.get('/api/bookings').json()['bookings'])==1


def test_eval_does_not_steal_active_call_or_accept_cross_origin_write(tmp_path):
    with TestClient(create_app(FakeRuntime,data_dir=tmp_path)) as client:
        client.get('/api/session')
        with client.websocket_connect('/ws') as ws:
            ws.receive_json()
            assert client.post('/api/eval/start').status_code==409
        assert client.post('/api/eval/start',headers={'origin':'https://evil.example'}).status_code==403
        assert client.post('/api/eval/stop',headers={'origin':'https://evil.example'}).status_code==403


def test_only_starting_caller_can_stop_evaluation(tmp_path,monkeypatch):
    import asyncio
    import server.app as module
    async def wait_forever(*args,**kwargs):await asyncio.Event().wait()
    monkeypatch.setattr(module,'run_suite',wait_forever)
    with TestClient(create_app(FakeRuntime,data_dir=tmp_path)) as client:
        client.get('/api/session');owner=client.cookies.get('eywa_caller')
        assert client.post('/api/eval/start').status_code==200
        client.cookies.clear();client.get('/api/session')
        assert client.post('/api/eval/stop').status_code==403
        client.cookies.set('eywa_caller',owner)
        assert client.post('/api/eval/stop').status_code==200


def test_expired_synthetic_runs_are_removed_without_touching_other_data(tmp_path):
    import os,time
    expired=tmp_path/'eval'/'20260101T000000Z-abcdef12'
    expired.mkdir(parents=True);(expired/'report.json').write_text('{}')
    unrelated=tmp_path/'eval'/'keep-this-folder';unrelated.mkdir()
    os.utime(expired,(time.time()-8*86400,)*2)
    with TestClient(create_app(FakeRuntime,data_dir=tmp_path)):
        assert not expired.exists() and unrelated.exists()
