"""Record the real clinic browser workflow using labelled synthetic caller input.

Playwright records video; FFmpeg muxes the actual generated/captured synthetic
PCM events. This is a reproducible fixture demo, not a recording of a human call.
"""
import asyncio
import argparse
import base64
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.audio import caller_audio
from pipeline.tts import PiperTTS


async def main(request):
    tts = PiperTTS()
    try:
        book = await caller_audio(tts, request)
        yes = await caller_audio(tts, "Yes.")
    finally:
        await tts.close()
    directory = ROOT / "runtime/demo"
    directory.mkdir(parents=True, exist_ok=True)
    probe = (ROOT / "benchmarks/phase1/browser_probe.js").read_text()
    extra = """(() => {
      const Existing = window.WebSocket;
      window.demoChunks = [];
      window.demoReadbacks = 0; window.demoCommitted = false;
      window.WebSocket = class extends Existing {
        constructor(...args) {
          super(...args);
          this.addEventListener('message', message => {
            const e = JSON.parse(message.data);
            if(e.type==='audio') window.demoChunks.push({...e,t:performance.now()});
            if(e.type==='booking_state' && e.success) window.demoCommitted = true;
            if(e.type==='clause_end') window.demoReadbacks++;
          });
        }
      };
    })()"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-audio-input", "--disable-audio-output", "--autoplay-policy=no-user-gesture-required"])
        state_path=directory/'browser-state.json'
        context = await browser.new_context(viewport={"width":1152,"height":900}, record_video_dir=str(directory), record_video_size={"width":1152,"height":900},
            storage_state=str(state_path) if state_path.exists() else None)
        await context.add_init_script(script=probe + "\n" + extra)
        page = await context.new_page()
        await page.goto("http://127.0.0.1:8000")
        await page.evaluate("""() => {
          const banner=document.createElement('div'); banner.textContent='RECORDED SYNTHETIC CALLER · REAL LOCAL INFERENCE';
          Object.assign(banner.style,{position:'fixed',bottom:'0',left:'0',right:'0',padding:'10px',background:'#c6d69f',color:'#162019',textAlign:'center',font:'12px system-ui',zIndex:'1000'});
          document.body.appendChild(banner);
        }""")
        await page.get_by_label("Conversation mode").select_option("connect")
        await page.get_by_role("button", name="Start voice call").click()
        await page.get_by_role("button", name="End call").wait_for()
        await page.evaluate("pcm=>window.eywaPlayFixture(pcm)", base64.b64encode(book).decode())
        await page.wait_for_function("window.demoReadbacks > 0", timeout=30000)
        # Confirmation cannot precede actual browser playback completion.
        await page.wait_for_function("window.eywaProbe.events.some(e=>e.type==='played')", timeout=30000)
        await page.evaluate("pcm=>window.eywaPlayFixture(pcm)", base64.b64encode(yes).decode())
        await page.wait_for_function("window.demoCommitted", timeout=30000)
        await page.wait_for_function("window.eywaProbe.events.filter(e=>e.type==='played').length >= 2", timeout=30000)
        await page.screenshot(path=str(ROOT/'docs/demo.png'),full_page=False)
        await page.wait_for_timeout(1000)
        raw = await page.evaluate("({probe:window.eywaProbe,chunks:window.demoChunks,elapsed:performance.now()})")
        await page.get_by_role("button", name="End call").click()
        await page.locator('.booking-panel').scroll_into_view_if_needed()
        await page.wait_for_timeout(1500)
        video = page.video
        await context.storage_state(path=str(state_path))
        await context.close()
        source = await video.path()
        await browser.close()
    length = round((raw["elapsed"] / 1000 + 2) * 16000)
    mix = np.zeros(length, dtype=float)
    def add(pcm, rate, at):
        values = np.frombuffer(pcm,dtype="<i2").astype(float)
        count = round(len(values)*16000/rate)
        values = np.interp(np.arange(count)*rate/16000,np.arange(len(values)),values)
        offset = round(at * 16)
        count = min(count,len(mix)-offset)
        if count > 0: mix[offset:offset+count] += values[:count] * 0.7
    for stimulus, pcm in zip(raw["probe"]["stimulus"],(book,yes)):
        add(pcm,16000,stimulus["t"])
    for chunk, output in zip(raw["chunks"],raw["probe"]["outputStarts"]):
        add(base64.b64decode(chunk["pcm"]),chunk["sample_rate"],output["t"])
    audio_path=directory/"demo.wav"
    with wave.open(str(audio_path),"wb") as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
        wav.writeframes(np.clip(mix,-32768,32767).astype("<i2").tobytes())
    destination=ROOT/"docs/demo.mp4"
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(source),"-i",str(audio_path),"-c:v","libx264","-preset","fast","-crf","25","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(destination)],check=True)
    print(destination)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',default='Book Tuesday at ten AM with Doctor Rao.')
    asyncio.run(main(parser.parse_args().request))
