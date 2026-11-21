"""Chromium capture/playback with a MockMicrophone and MOCK inference.

The OS/device acquisition boundary is replaced by a real WebAudio MediaStream.
The production AudioWorklet, PCM WebSocket transport and playback run unchanged.
Native getUserMedia acquisition is a separate, still-unvalidated hardware check.
"""
import socket
import os
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from server.app import create_app
from tests.fakes import FakeRuntime


@pytest.fixture(scope="module")
def mock_server(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    runtime = FakeRuntime()
    runtime.received_frames = 0
    def probability(pcm):
        runtime.received_frames += 1
        return 0  # Mock microphone must not create turns itself.
    runtime.vad.probability = probability
    data_dir=tmp_path_factory.mktemp('browser-app')
    report_dir=data_dir/'eval'/'20261121T000000Z-abcdef12'
    report_dir.mkdir(parents=True)
    import json
    (report_dir/'report.json').write_text(json.dumps({'id':report_dir.name,'status':'completed','mode':'MOCK dashboard fixture',
        'transport':'MOCK test report; no performance claim','summary':{'scenario_count':1,'task_success_rate':1,'false_confirmation_rate':0,'duplicate_write_rate':0,'asr_wer':0,'voice_to_voice_ms':{'p50':100,'p95':200}},
        'scenarios':[{'id':'mock_case','task_success':True,'asr_wer':0,'trace':[{'role':'agent','text':'MOCK replay transcript'}],'state':[],'stage_metrics':[],'barge_in':[]}]}))
    server = uvicorn.Server(uvicorn.Config(create_app(lambda: runtime,data_dir=data_dir), host="127.0.0.1",
        port=port, log_level="error", ws_max_size=4096))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("MOCK browser server did not start")
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}", runtime
    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.fixture
def browser_page(mock_server):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("EYWA_TEST_CHROMIUM"), headless=True, args=[
            "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
            "--disable-audio-input", "--disable-audio-output",
            "--autoplay-policy=no-user-gesture-required",
        ])
        context = browser.new_context(permissions=["microphone"])
        context.add_init_script("""(() => {
          // Explicit MockMicrophone: never claim this tests a physical device.
          navigator.mediaDevices.getUserMedia = async constraints => {
            window.mockMicrophoneConstraints = constraints;
            const audio = new AudioContext({sampleRate: 16000});
            const destination = audio.createMediaStreamDestination();
            const oscillator = audio.createOscillator();
            const silent = audio.createGain(); silent.gain.value = 0;
            oscillator.connect(silent).connect(destination); oscillator.start();
            await audio.resume();
            window.mockMicrophoneTracks = destination.stream.getTracks();
            window.mockMicrophoneContext = audio;
            return destination.stream;
          };
        })()""")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(mock_server[0])
        yield page
        browser.close()
        assert not errors


def test_mock_microphone_capture_transport_stop_and_mock_label(browser_page, mock_server):
    page = browser_page
    before = mock_server[1].received_frames
    page.get_by_role("button", name="Start voice call").click()
    expect(page.get_by_role("button", name="End call")).to_be_visible()
    expect(page.get_by_text("MOCK · TEST SESSION", exact=True)).to_be_visible()
    expect(page.get_by_role("status")).to_have_text("Listening")
    assert page.evaluate("window.mockMicrophoneConstraints.audio.echoCancellation") is True
    # Wait for the actual worklet to send PCM frames over the real WebSocket.
    deadline = time.monotonic() + 3
    while mock_server[1].received_frames <= before:
        assert time.monotonic() < deadline, "AudioWorklet did not send microphone PCM"
        time.sleep(0.01)
    page.get_by_role("button", name="End call").click()
    expect(page.get_by_role("status")).to_have_text("Call ended")
    assert page.evaluate("window.mockMicrophoneTracks.every(t => t.readyState === 'ended')")
    page.evaluate("window.mockMicrophoneContext.close()")


def test_pending_microphone_can_be_cancelled_and_late_stream_is_stopped(browser_page):
    page = browser_page
    page.evaluate("""() => {
      navigator.mediaDevices.getUserMedia = () => new Promise(resolve => {
        window.resolveMockMicrophone = resolve;
      });
    }""")
    page.get_by_role("button", name="Start voice call").click()
    expect(page.get_by_role("status")).to_have_text("Requesting microphone permission")
    page.get_by_role("button", name="Cancel connection").click()
    expect(page.get_by_role("status")).to_have_text("Call ended")
    page.evaluate("""() => {
      const audio = new AudioContext();
      const stream = audio.createMediaStreamDestination().stream;
      window.lateMockTracks = stream.getTracks();
      window.resolveMockMicrophone(stream);
      audio.close();
    }""")
    page.wait_for_function("window.lateMockTracks.every(t => t.readyState === 'ended')")
    expect(page.get_by_role("status")).to_have_text("Call ended")


def test_browser_stops_queued_audio_and_cannot_acknowledge_unplayed_clause(browser_page):
    # Exercise the exact production playback module in a browser, including the
    # source.stop()/onended race that can otherwise retain unspoken history.
    page = browser_page
    # Vite's bundle does not export internal symbols. Compile the exact production
    # playback source and evaluate it in the real browser for this focused check.
    from pathlib import Path
    import subprocess
    root = Path(__file__).resolve().parents[1]
    subprocess.run([str(root / "web/node_modules/.bin/tsc"),
        str(root / "web/src/audio.ts"), "--target", "ES2022", "--module", "ESNext",
        "--skipLibCheck", "--outDir", str(root / "runtime/browser-test")],
        capture_output=True, text=True, check=True)
    source = (root / "runtime/browser-test/audio.js").read_text().replace("export class Playback", "class Playback")
    result = page.evaluate("""async (source) => {
      const Playback = new Function(source + '; return Playback;')();
      const context = new AudioContext({sampleRate: 16000}); await context.resume();
      const player = new Playback(context);
      let ack = 0;
      player.push(btoa('\\0'.repeat(32000)), 16000, 0);
      player.endClause(0, () => ack++);
      const queued = player.sources.size;
      player.stop();
      await new Promise(resolve => setTimeout(resolve, 100));
      const stopped = player.sources.size;
      const staleAck = ack;
      player.push(btoa('\\0'.repeat(1600)), 16000, 1);
      player.endClause(1, () => ack++);
      await new Promise(resolve => setTimeout(resolve, 200));
      await context.close();
      return {queued, stopped, staleAck, ack};
    }""", source)
    assert result == {"queued": 1, "stopped": 0, "staleAck": 0, "ack": 1}


def test_capture_keeps_sending_silence_when_input_bus_has_no_channels(browser_page):
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "web/public/capture.js").read_text()
    result = browser_page.evaluate("""source => {
      const sent = [];
      let Capture;
      class MockProcessor {constructor() {this.port = {postMessage: pcm => sent.push(pcm)};}}
      new Function('AudioWorkletProcessor', 'registerProcessor', source)(MockProcessor,
        (_name, Type) => Capture = Type);
      const node = new Capture();
      for (let i = 0; i < 4; i++) node.process([[]], [[new Float32Array(128)]]);
      return {frames: sent.length, samples: new Int16Array(sent[0]).length,
        silence: new Int16Array(sent[0]).every(sample => sample === 0)};
    }""", source)
    assert result == {"frames": 1, "samples": 512, "silence": True}


def test_late_socket_events_cannot_restart_an_ended_call(browser_page):
    page = browser_page
    page.evaluate("""() => {
      class MockSocket {
        static OPEN = 1;
        readyState = 1;
        bufferedAmount = 0;
        constructor() {
          window.lateSocket = this;
          setTimeout(() => this.emit({type:'ready', mode:'MOCK test socket'}), 0);
        }
        send() {}
        close() { this.readyState = 3; }
        emit(event) { this.onmessage?.({data: JSON.stringify(event)}); }
      }
      window.WebSocket = MockSocket;
    }""")
    page.get_by_role("button", name="Start voice call").click()
    expect(page.get_by_role("button", name="End call")).to_be_visible()
    page.get_by_role("button", name="End call").click()
    page.evaluate("""() => {
      window.lateSocket.emit({type:'transcript', turn_id:2, text:'late buffered text'});
      window.lateSocket.emit({type:'audio', turn_id:2, clause_id:0, sample_rate:22050,
        pcm:btoa('\\0'.repeat(1600))});
    }""")
    expect(page.get_by_role("status")).to_have_text("Call ended")
    expect(page.get_by_text("late buffered text")).not_to_be_visible()
    page.evaluate("window.mockMicrophoneContext.close()")


def test_dashboard_displays_report_metrics_replay_and_scenario_details(browser_page):
    page=browser_page
    page.get_by_role('button',name='20261121T000000Z-abcdef12',exact=False).click()
    expect(page.get_by_role('heading',name='Run 20261121T000000Z-abcdef12')).to_be_visible()
    expect(page.get_by_text('False confirmation',exact=True)).to_be_visible()
    page.locator('summary',has_text='mock_case').click()
    expect(page.get_by_text('MOCK replay transcript',exact=False)).to_be_visible()
    expect(page.locator('audio')).to_have_attribute('src','/api/eval/audio/20261121T000000Z-abcdef12/mock_case')
    expect(page.get_by_role('link',name='Download JSON report')).to_be_visible()


def test_phone_and_booking_controls_are_available_without_bypassing_confirmation(browser_page):
    page=browser_page
    page.get_by_label('Conversation mode').select_option('connect')
    expect(page.get_by_role('heading',name='Verified appointment workflow')).to_be_visible()
    page.get_by_label('Phone quality · G.711').check()
    expect(page.get_by_label('Ingress delay')).to_be_visible()
    page.get_by_role('button',name='Start voice call').click()
    expect(page.get_by_role('button',name='Press 1 · confirm')).to_be_visible()
    expect(page.get_by_role('button',name='Run 30 voice scenarios')).to_be_disabled()
    page.get_by_role('button',name='Hold',exact=True).click()
    expect(page.get_by_role('button',name='Resume',exact=True)).to_be_visible()
    page.get_by_role('button',name='Resume',exact=True).click()
    page.get_by_role('button',name='End call').click()
    page.evaluate('window.mockMicrophoneContext.close()')
