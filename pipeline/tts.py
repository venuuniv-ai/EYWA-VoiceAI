"""Resident subprocess: terminate native Piper work on cancellation, then reload lazily."""
import asyncio
import base64
import json
import sys
from pathlib import Path

from pipeline.admission import MB, check_admission
from pipeline.profiling import measured_command, signal_owned

ROOT = Path(__file__).resolve().parents[1]


class PiperTTS:
    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.profiled = False
        self.warming: asyncio.Task | None = None
        self.worker_number = 0

    async def start(self):
        if self.proc is not None and self.proc.returncode is None:
            return
        check_admission("Piper worker", 223 * MB)
        self.worker_number += 1
        command, self.profiled = measured_command([sys.executable, "-m", "pipeline.piper_worker",
            str(ROOT / "models/tts/en_US-libritts_r-medium.onnx")], f"piper-{self.worker_number}")
        self.proc = await asyncio.create_subprocess_exec(*command, cwd=ROOT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=1024 * 1024)
        try:
            ready = await asyncio.wait_for(self.proc.stdout.readline(), 30)
            if not ready or not json.loads(ready).get("ready"):
                raise RuntimeError("Piper worker did not become ready")
        except BaseException:
            await self._stop_worker()
            raise

    def prepare(self):
        """Speculatively reload after barge-in while the caller is still speaking."""
        if self.proc is not None or self.warming is not None:
            return
        async def warm():
            async with self.lock:
                await self.start()
        task = self.warming = asyncio.create_task(warm())
        def finished(done):
            if self.warming is done:
                self.warming = None
            if not done.cancelled():
                # Speculative failure must not become an unhandled task error.
                # The next requested synthesis retries admission and reports failure.
                done.exception()
        task.add_done_callback(finished)

    async def close(self):
        warming, self.warming = self.warming, None
        if warming is not None:
            warming.cancel()
            try:
                await warming
            except asyncio.CancelledError:
                pass
            except Exception:
                pass  # Speculative reload failure must not prevent worker cleanup.
        await self._stop_worker()

    async def _stop_worker(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.returncode is None:
            signal_owned(proc.pid, self.profiled)
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            signal_owned(proc.pid, self.profiled, force=True)
            await proc.wait()

    async def stream(self, text: str, speaker: int = 0):
        async with self.lock:
            complete = False
            try:
                await self.start()
                self.proc.stdin.write(json.dumps({"text": text, "speaker": speaker}).encode() + b"\n")
                await self.proc.stdin.drain()
                while True:
                    line = await asyncio.wait_for(self.proc.stdout.readline(), 30)
                    if not line:
                        raise RuntimeError("Piper worker exited")
                    event = json.loads(line)
                    if event.get("done"):
                        complete = True
                        break
                    if event.get("error"):
                        raise RuntimeError(event["error"])
                    yield event["rate"], base64.b64decode(event["pcm"])
            finally:
                # Cancelling a Python thread does not stop ONNX. Killing this owned
                # worker does, including when an async generator is explicitly closed.
                if not complete:
                    await self._stop_worker()
