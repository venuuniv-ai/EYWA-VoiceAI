"""Loopback-only FastAPI server and PCM16 WebSocket gateway."""
import asyncio
import json
import logging
import os
import time
import shutil
import re
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from server.runtime import ROOT, Runtime
from server.session import VoiceSession
from connect.store import BookingStore
from connect.workflow import BookingWorkflow
from connect.telephony import CallState, PhoneCodec, PhoneSettings, IngressLine
from eval.runner import run_suite


def create_app(runtime_factory=Runtime, data_dir=None):
    data = Path(data_dir or os.environ.get("EYWA_DATA_DIR", ROOT / "runtime"))
    @asynccontextmanager
    async def lifespan(app):
        runtime = runtime_factory()
        app.state.runtime, app.state.busy = runtime, False
        app.state.bookings = BookingStore(data / "bookings.sqlite3")
        app.state.eval_status = {"status": "idle", "completed": 0, "total": 30}
        app.state.eval_task = None
        app.state.eval_owner = None
        for old in (data / "eval").glob("*"):
            if (re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{8}", old.name) and old.is_dir()
                    and not old.is_symlink() and old.stat().st_mtime < time.time() - 7 * 86400):
                shutil.rmtree(old)
        try:
            await runtime.start()
            logging.getLogger("eywa").warning("EYWA inference mode: %s", runtime.mode)
            yield
        finally:
            if app.state.eval_task and not app.state.eval_task.done():
                if not app.state.eval_task.cancelling():
                    app.state.eval_task.cancel()
                try:
                    await app.state.eval_task
                except asyncio.CancelledError:
                    pass
            await runtime.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.get("/health")
    async def health():
        return {"status": "ready", "mode": app.state.runtime.mode,
                "call_active": app.state.busy, "audio_storage": False}

    def caller(request):
        identity = app.state.bookings.resolve(request.cookies.get("eywa_caller"))
        if not identity:
            raise HTTPException(401, "Establish a local caller session first")
        return identity

    @app.get("/api/session")
    async def establish(request: Request, response: Response):
        token = request.cookies.get("eywa_caller")
        identity = app.state.bookings.resolve(token)
        if not identity:
            identity, token = app.state.bookings.identity()
            response.set_cookie("eywa_caller", token, httponly=True, samesite="strict", max_age=30 * 86400)
        return {"authenticated": True, "scope": "local demo caller; not real-world identity verification"}

    @app.get("/api/bookings")
    async def bookings(request: Request):
        return {"bookings": app.state.bookings.list(caller(request))}

    @app.get("/api/eval/status")
    async def evaluation_status(request: Request):
        caller(request)
        return app.state.eval_status

    @app.post("/api/eval/start")
    async def evaluation_start(request: Request):
        owner = caller(request)
        if request.headers.get("origin") and urlsplit(request.headers["origin"]).netloc != request.headers.get("host"):
            raise HTTPException(403, "Same-origin requests only")
        if app.state.busy:
            raise HTTPException(409, "End the active call or evaluation first")
        app.state.busy = True
        app.state.eval_owner = owner
        app.state.eval_status = {"status": "running", "completed": 0, "total": 30}
        def progress(completed, total, scenario):
            app.state.eval_status = {"status": "running", "completed": completed, "total": total, "scenario": scenario}
        async def evaluate():
            try:
                report = await run_suite(app.state.runtime, data / "eval", seed=42, progress=progress)
                app.state.eval_status = {"status": "completed", "completed": 30, "total": 30, "id": report["id"]}
            except asyncio.CancelledError:
                app.state.eval_status = {**app.state.eval_status, "status": "cancelled"}
                raise
            except Exception as exc:
                app.state.eval_status = {**app.state.eval_status, "status": "failed", "error_class": type(exc).__name__}
            finally:
                app.state.busy = False
                app.state.runtime.vad.reset()
        app.state.eval_task = asyncio.create_task(evaluate())
        return {"started": True, "seed": 42}

    @app.post("/api/eval/stop")
    async def evaluation_stop(request: Request):
        owner = caller(request)
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Same-origin requests only")
        if app.state.eval_task and not app.state.eval_task.done():
            if owner != app.state.eval_owner:
                raise HTTPException(403, "Only the caller that started this evaluation can stop it")
            if not app.state.eval_task.cancelling():
                app.state.eval_task.cancel()
        return {"stopping": True}

    def report_path(run_id, filename="report.json"):
        import re
        if not re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{8}", run_id):
            raise HTTPException(404)
        path = data / "eval" / run_id / filename
        if not path.is_file():
            raise HTTPException(404)
        return path

    @app.get("/api/eval/reports")
    async def evaluation_reports(request: Request):
        caller(request)
        reports = []
        for path in sorted((data / "eval").glob("*/report.json"), reverse=True)[:30]:
            try:
                value = json.loads(path.read_text())
                reports.append({k:value.get(k) for k in ("id", "status", "mode", "summary")})
            except (ValueError, OSError):
                continue
        return {"reports": reports}

    @app.get("/api/eval/reports/{run_id}")
    async def evaluation_report(request: Request, run_id: str):
        caller(request)
        return FileResponse(report_path(run_id), media_type="application/json")

    @app.get("/api/eval/audio/{run_id}/{scenario}")
    async def evaluation_audio(request: Request, run_id: str, scenario: str):
        import re
        caller(request)
        if not re.fullmatch(r"[a-z_]+", scenario):
            raise HTTPException(404)
        return FileResponse(report_path(run_id, scenario + ".wav"), media_type="audio/wav")

    @app.websocket("/ws")
    async def voice(ws: WebSocket):
        origin = ws.headers.get("origin")
        # Reject cross-origin browser access; local non-browser benchmark clients
        # may omit Origin. TrustedHost also protects against DNS rebinding.
        if origin and (urlsplit(origin).scheme not in ("http", "https")
                       or urlsplit(origin).netloc != ws.headers.get("host")):
            await ws.close(code=1008)
            return
        if app.state.busy:
            await ws.close(code=1013)
            return
        mode = ws.query_params.get("mode", "live")
        if mode not in {"live", "connect"}:
            await ws.close(code=1008)
            return
        identity = None
        if mode == "connect":
            identity = app.state.bookings.resolve(ws.cookies.get("eywa_caller"))
            if not identity:
                await ws.close(code=1008, reason="Caller session required")
                return
        phone = ws.query_params.get("phone") == "1"
        try:
            settings = PhoneSettings(latency_ms=int(ws.query_params.get("latency", 0)),
                jitter_ms=int(ws.query_params.get("jitter", 0)), loss=float(ws.query_params.get("loss", 0)),
                seed=int(ws.query_params.get("seed", 0)))
        except ValueError:
            await ws.close(code=1008, reason="Invalid phone settings")
            return
        app.state.busy = True
        lock = asyncio.Lock()
        call_state = CallState()
        outgoing_codec = PhoneCodec()

        async def send(event):
            if phone and event.get("type") == "audio":
                import base64
                event = {**event, "sample_rate": 16000,
                    "pcm": base64.b64encode(outgoing_codec.process(base64.b64decode(event["pcm"]), event["sample_rate"])).decode()}
            async with lock:
                await ws.send_json(event)

        workflow = BookingWorkflow(app.state.bookings, identity, app.state.runtime.llm) if mode == "connect" else None
        session = VoiceSession(app.state.runtime, send, workflow)
        async def deliver(pcm):
            if call_state.state == "connected":
                await session.feed(pcm)
        line = IngressLine(settings, deliver) if phone else None
        app.state.runtime.vad.reset()
        try:
            await ws.accept()
            await send({"type": "ready", "mode": app.state.runtime.mode,
                        "sample_rate": 16000, "frame_samples": 512, "workflow": mode, "call_state": "connected"})
            if mode == "connect" or phone:
                await send({"type": "call_state", "state": "ringing"})
            call_state.change("connected")
            if mode == "connect" or phone:
                await send({"type": "call_state", "state": "connected"})
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    pcm = message["bytes"]
                    if len(pcm) != 1024:
                        await ws.close(code=1003, reason="Expected 512 PCM16 samples")
                        break
                    if line:
                        if line.task.done():
                            line.task.result()
                        await line.put(pcm)
                    else:
                        await deliver(pcm)
                elif message.get("text") is not None:
                    if len(message["text"]) > 2048:
                        await ws.close(code=1009)
                        break
                    event = json.loads(message["text"])
                    if event.get("type") == "interrupt":
                        await session.interrupt()
                    elif event.get("type") == "played":
                        turn_id, clause_id = event.get("turn_id"), event.get("clause_id")
                        if type(turn_id) is int and type(clause_id) is int:
                            await session.acknowledge(turn_id, clause_id)
                    elif event.get("type") == "stop":
                        break
                    elif event.get("type") == "call_state":
                        call_state.change(event.get("state"))
                        await session.interrupt()
                        session.endpoint.reset()
                        await send({"type": "call_state", "state": call_state.state})
                        if call_state.state == "hangup":
                            break
                    elif event.get("type") == "dtmf" and event.get("digit") in {"0", "1"}:
                        if call_state.state == "connected" and workflow:
                            await session.control_digit(event["digit"])
                    else:
                        await ws.close(code=1003, reason="Unknown control message")
                        break
        except (WebSocketDisconnect, RuntimeError):
            pass
        except (ValueError, TypeError, AttributeError):
            await ws.close(code=1003, reason="Invalid control message")
        finally:
            try:
                try:
                    if line:
                        await line.close()
                finally:
                    await session.close()
            finally:
                app.state.busy = False
                app.state.runtime.vad.reset()

    dist = ROOT / "web/dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/capture.js")
        async def capture():
            return FileResponse(dist / "capture.js", media_type="text/javascript")

        @app.get("/")
        async def index():
            return FileResponse(dist / "index.html")

    return app


app = create_app()
