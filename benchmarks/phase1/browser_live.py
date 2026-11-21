"""Real inference + production browser AudioWorklet/playback, fixture input device.

Reports WebAudio scheduling/stop dispatch, never acoustic hardware loopback.
"""
import base64
import hashlib
import wave
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright
import httpx

ROOT = Path(__file__).resolve().parents[2]


class BrowserProbeError(RuntimeError):
    def __init__(self, diagnostic):
        super().__init__("Browser probe failed; see saved diagnostic")
        self.diagnostic = diagnostic


def pcm_fixture():
    path = ROOT / "benchmarks/phase0/audio/caller_short.wav"
    with wave.open(str(path)) as wav:
        pcm = wav.readframes(wav.getnframes())
    return base64.b64encode(pcm).decode(), hashlib.sha256(path.read_bytes()).hexdigest()


async def probe(url, repetitions=3):
    from benchmarks.phase0.run_phase0 import pct
    pcm, checksum = pcm_fixture()
    result = {"mode": "REAL inference; synthetic caller WebAudio input device",
        "fixture_sha256": checksum, "physical_microphone": False,
        "latency_boundary": "last server Silero speech frame captured by browser to first scheduled WebAudio output",
        "barge_boundary": "first captured RMS>0.025 frame after injected speech to AudioBufferSourceNode.stop dispatch",
        "physical_output_latency_included": False, "runs": []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-audio-input", "--disable-audio-output", "--autoplay-policy=no-user-gesture-required"])
        result["browser"] = browser.version
        page = None
        stage = "setup"
        try:
            for i in range(repetitions):
                stage = "waiting_for_call_slot"
                async with httpx.AsyncClient(trust_env=False) as health:
                    async with asyncio.timeout(15):
                        while (await health.get(url + "/health")).json()["call_active"]:
                            await asyncio.sleep(0.05)
                context = await browser.new_context()
                await context.add_init_script(path=str(ROOT / "benchmarks/phase1/browser_probe.js"))
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(url)
                stage = "starting_call"
                await page.get_by_role("button", name="Start voice call").click()
                await page.get_by_role("button", name="End call").wait_for()
                await page.evaluate("pcm => window.eywaPlayFixture(pcm)", pcm)
                stage = "first_agent_audio"
                await page.wait_for_function("window.eywaProbe.outputStarts.length > 0", timeout=30000)
                # Barge in while actual Piper output is scheduled/playing.
                await page.wait_for_timeout(80)
                await page.evaluate("pcm => window.eywaPlayFixture(pcm)", pcm)
                stage = "barge_in_stop"
                await page.wait_for_function("window.eywaProbe.stops.length > 0", timeout=10000)
                stage = "recovered_response"
                await page.wait_for_function("""() => {
                  const s = window.eywaProbe;
                  const original = s.events.find(e => e.type === 'audio');
                  return s.events.some(e => e.type === 'response_done' && e.turn_id > original.turn_id);
                }""", timeout=45000)
                raw = await page.evaluate("window.eywaProbe")
                assert raw["requestedEchoCancellation"] is True
                assert not errors, errors
                assert not any(e["type"] == "error" for e in raw["events"])
                # The completed post-interruption turn proves the same call recovered.
                original_turn = next(e["turn_id"] for e in raw["events"] if e["type"] == "audio")
                completed = next(e for e in raw["events"] if e["type"] == "response_done" and e["turn_id"] > original_turn)
                speech_frame = completed["metrics"]["speech_end_frame"]
                last_speech_t = raw["frames"][speech_frame - 1]["t"]
                first_audio = next(e for e in raw["events"] if e["type"] == "audio"
                                   and e["turn_id"] == completed["turn_id"])
                first_output = next(e for e in raw["outputStarts"]
                                    if e["turn_id"] == completed["turn_id"] and e["t"] >= first_audio["t"])
                onset_index = next(j for j in range(raw["stimulus"][1]["frame"], len(raw["frames"]))
                                   if raw["frames"][j]["rms"] > 0.025)
                onset = raw["frames"][onset_index]["t"]
                stop = next(e["t"] for e in raw["stops"] if e["t"] >= onset)
                interrupted = [e for e in raw["events"] if e["type"] == "interrupted" and e["t"] >= onset]
                result["runs"].append({"round": i,
                    "browser_voice_to_voice_ms": first_output["t"] - last_speech_t,
                    "barge_in_stop_dispatch_ms": stop - onset,
                    "barge_onset_uncertainty_ms": 32,
                    "server_cancel_ms": interrupted[0]["server_cancel_ms"],
                    "inflight_generation_cancelled": any(e.get("cancelled_generation") for e in interrupted),
                    "recovered_turn": True, "stage_metrics": completed["metrics"]})
                await page.get_by_role("button", name="End call").click()
                await context.close()
        except Exception as exc:
            result["failure"] = {"stage": stage, "error_class": type(exc).__name__}
            if page and not page.is_closed():
                result["failure"]["status"] = await page.get_by_role("status").inner_text()
                result["failure"]["probe"] = await page.evaluate("window.eywaProbe")
            raise BrowserProbeError(result) from exc
        finally:
            await browser.close()
    result["summary"] = {key: pct([run[key] for run in result["runs"]])
        for key in ("browser_voice_to_voice_ms", "barge_in_stop_dispatch_ms", "server_cancel_ms")}
    return result
