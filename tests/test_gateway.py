import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.app import create_app
from tests.fakes import FakeRuntime


@pytest.fixture
def client():
    with TestClient(create_app(FakeRuntime)) as c:
        yield c


def test_health_explicitly_identifies_mocks(client):
    assert "MOCK" in client.get("/health").json()["mode"]


def test_only_one_call_and_disconnect_releases_slot(client):
    with client.websocket_connect("/ws") as ws:
        assert "MOCK" in ws.receive_json()["mode"]
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws"):
                pass
        assert exc.value.code == 1013
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "ready"


@pytest.mark.parametrize("payload", [b"wrong frame", "not JSON", '[]', '{"type":"unknown"}'])
def test_bad_protocol_messages_close_cleanly(client, payload):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(payload) if isinstance(payload, bytes) else ws.send_text(payload)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1003


def test_cross_origin_access_refused(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
            pass
    assert exc.value.code == 1008


def test_pcm_turn_emits_audio_and_metrics(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        for frame in [b"\1\0" * 512] * 10 + [b"\0" * 1024] * 15:
            ws.send_bytes(frame)
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "clause_end":
                ws.send_json({"type": "played", "turn_id": event["turn_id"], "clause_id": event["clause_id"]})
            if event["type"] == "response_done":
                break
        assert any(e["type"] == "audio" for e in events)
        assert any(e["type"] == "transcript" for e in events)
