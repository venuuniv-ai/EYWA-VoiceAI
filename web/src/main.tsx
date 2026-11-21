import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Playback} from './audio';
import './style.css';
import {Review} from './Review';

type Line = {role: string; text: string};
type Call = {ws: WebSocket; context: AudioContext; stream: MediaStream; capture: AudioWorkletNode; player: Playback};

function App() {
  const [status, setStatus] = useState('Ready to connect');
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [metrics, setMetrics] = useState<Record<string, number>>({});
  const [error, setError] = useState('');
  const [mode, setMode] = useState('REAL · LOCAL INFERENCE');
  const call = useRef<Call | null>(null);
  const attempt = useRef(0);
  const [workflowMode, setWorkflowMode] = useState('live');
  const [phone, setPhone] = useState(false);
  const [latency, setLatency] = useState(0);
  const [jitter, setJitter] = useState(0);
  const [loss, setLoss] = useState(0);
  const [callState, setCallState] = useState('hangup');
  const [booking, setBooking] = useState<any>(null);
  const [bookings, setBookings] = useState<any[]>([]);
  const [turns, setTurns] = useState<any[]>([]);
  const transcript = useRef<HTMLDivElement>(null);
  useEffect(() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; }, [lines]);
  async function refreshBookings() {
    const response = await fetch('/api/bookings');
    if (response.ok) setBookings((await response.json()).bookings);
  }
  useEffect(() => { void fetch('/api/session').then(refreshBookings).catch(() => {}); }, []);
  function control(event: object) {
    if (call.current?.ws.readyState === WebSocket.OPEN) call.current.ws.send(JSON.stringify(event));
  }

  function stop() {
    attempt.current++;
    const current = call.current; call.current = null;
    if (current) {
      current.player.stop(); current.capture.disconnect();
      current.capture.port.onmessage = null;
      current.stream.getTracks().forEach(t => t.stop());
      current.ws.close(); void current.context.close();
    }
    setActive(false); setStarting(false); setCallState('hangup'); setStatus('Call ended');
  }

  async function start() {
    const thisAttempt = ++attempt.current;
    const checkAttempt = () => {
      if (attempt.current !== thisAttempt) throw new DOMException('Call start cancelled', 'AbortError');
    };
    setStarting(true); setError(''); setLines([]); setMetrics({}); setTurns([]); setBooking(null);
    let stream: MediaStream | undefined, context: AudioContext | undefined, ws: WebSocket | undefined;
    try {
      const identity = await fetch('/api/session');
      if (!identity.ok) throw new Error('Unable to establish a local caller session');
      checkAttempt();
      setStatus('Requesting microphone permission');
      stream = await navigator.mediaDevices.getUserMedia({audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
      }});
      checkAttempt();
      setStatus('Initializing audio');
      context = new AudioContext({sampleRate: 16000});
      await context.resume();
      checkAttempt();
      if (context.sampleRate !== 16000) throw new Error('This browser cannot provide 16 kHz capture. Try Chrome.');
      setStatus('Loading microphone capture');
      await context.audioWorklet.addModule('/capture.js');
      checkAttempt();
      const player = new Playback(context);
      const capture = new AudioWorkletNode(context, 'eywa-capture');
      context.createMediaStreamSource(stream).connect(capture);
      // The worklet outputs zeros. Connecting it keeps capture processing alive.
      capture.connect(context.destination);
      const options = new URLSearchParams({mode:workflowMode, phone:phone ? '1' : '0', latency:String(latency), jitter:String(jitter), loss:String(loss / 100), seed:'42'});
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws?${options}`);
      const socket = ws;
      let blockedThrough = -1, currentTurn = -1, energyFrames = 0;
      let interrupted = false, done = false;
      call.current = {ws, context, stream, capture, player};
      setStatus('Connecting to local models');
      capture.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (socket.readyState !== WebSocket.OPEN) return;
        const samples = new Int16Array(event.data);
        const rms = Math.sqrt(samples.reduce((sum, x) => sum + (x / 32768) ** 2, 0) / samples.length);
        energyFrames = rms > 0.025 ? energyFrames + 1 : 0;
        // Stop locally without waiting for a server round trip. Server Silero
        // separately detects speech and fences/cancels the inference tasks.
        if (energyFrames >= 2 && player.sources.size && !interrupted) {
          const detected = performance.now();
          player.stop(); blockedThrough = Math.max(blockedThrough, currentTurn);
          interrupted = true;
          socket.send(JSON.stringify({type: 'interrupt'}));
          setMetrics(m => ({...m, client_stop_dispatch_ms: performance.now() - detected}));
          setStatus('Listening — interrupted');
        }
        if (socket.bufferedAmount > 64000) {
          setError('Audio connection fell behind. Restart the call.'); stop(); return;
        }
        socket.send(event.data);
      };
      socket.onmessage = event => {
        if (call.current?.ws !== socket) return; // Ignore buffered events after End call.
        const message = JSON.parse(event.data);
        const turn = message.turn_id as number;
        if (message.type === 'ready') { setMode(message.mode); setCallState(message.call_state ?? 'connected'); setActive(true); setStarting(false); setStatus('Listening'); return; }
        if (message.type === 'call_state') { setCallState(message.state); return; }
        if (message.type === 'interrupted') {
          blockedThrough = Math.max(blockedThrough, turn);
          player.stop(); return;
        }
        if (message.type === 'speech_started') { setStatus('Listening'); return; }
        if (turn <= blockedThrough) return;
        currentTurn = turn;
        if (message.type === 'booking_state') {
          setBooking(message);
          if (message.success) void refreshBookings();
          return;
        }
        if (message.type === 'state') { setStatus('Recognizing speech'); done = false; }
        if (message.type === 'transcript') {
          setLines(previous => [...previous.slice(-29), {role: 'You', text: message.text}]);
          setStatus('Thinking');
        }
        if (message.type === 'audio') {
          interrupted = false;
          player.push(message.pcm, message.sample_rate, message.clause_id);
          setStatus('Speaking — you can interrupt');
        }
        if (message.type === 'clause_end') player.endClause(message.clause_id, () => {
          if (socket.readyState === WebSocket.OPEN && turn > blockedThrough) {
            socket.send(JSON.stringify({type: 'played', turn_id: turn, clause_id: message.clause_id}));
            setLines(previous => [...previous.slice(-29), {role: 'EYWA', text: message.text}]);
            if (done && !player.sources.size) setStatus('Listening');
          }
        });
        if (message.type === 'response_done') {
          done = true; setMetrics(message.metrics);
          setTurns(previous => [...previous.slice(-19), {id:turn, metrics:message.metrics}]);
          if (!player.sources.size) setStatus('Listening');
        }
        if (message.type === 'empty_speech') setStatus('Listening — no speech recognized');
        if (message.type === 'error') {
          setError(message.message); player.stop(); setStatus('Listening — try again');
        }
      };
      socket.onerror = () => {
        if (call.current?.ws === socket) { setError('Local voice connection failed. Check the server terminal.'); stop(); }
      };
      socket.onclose = event => {
        if (call.current?.ws === socket) {
          stop(); setStatus(event.code === 1013 ? 'Another call is already active' : 'Disconnected');
        }
      };
    } catch (e) {
      if (call.current?.ws === ws && ws) stop();
      else { stream?.getTracks().forEach(t => t.stop()); void context?.close(); ws?.close(); }
      if (attempt.current === thisAttempt) {
        setError(e instanceof Error ? e.message : 'Unable to start microphone');
        setStatus('Unable to connect');
      }
    } finally { if (attempt.current === thisAttempt && !call.current) setStarting(false); }
  }

  return <main className={active ? 'in-call' : ''}>
    <header><a className="wordmark" href="/">EYWA<span> / LIVE</span></a><span className="badge">{mode.startsWith('MOCK') ? 'MOCK · TEST SESSION' : mode.includes('compact:') ? 'LOCAL · QWEN 0.5B' : 'LOCAL · QWEN 1.5B'}</span></header>
    <section className="intro"><p className="eyebrow">A conversation, on your machine.</p><h1>Speak freely.<br/><span>Stay in control.</span></h1><p>Real speech recognition, local reasoning, and a voice you can interrupt.</p></section>
    <section className="call"><div className="configuration">
      <label>Conversation <select aria-label="Conversation mode" disabled={active || starting} value={workflowMode} onChange={e => setWorkflowMode(e.target.value)}><option value="live">EYWA Live</option><option value="connect">Fictional clinic booking</option></select></label>
      <label className="check"><input type="checkbox" checked={phone} disabled={active || starting} onChange={e => setPhone(e.target.checked)}/> Phone quality · G.711</label>
      {phone && <div className="phone-settings"><label>Ingress delay (ms)<input aria-label="Ingress delay" type="number" min="0" max="500" value={latency} disabled={active || starting} onChange={e => setLatency(Number(e.target.value))}/></label><label>Jitter (ms)<input type="number" min="0" max="200" value={jitter} disabled={active || starting} onChange={e => setJitter(Number(e.target.value))}/></label><label>Loss (%)<input type="number" min="0" max="50" value={loss} disabled={active || starting} onChange={e => setLoss(Number(e.target.value))}/></label></div>}
    </div><div className={`orb ${active ? 'active' : ''}`} aria-hidden="true">◌</div><p role="status">{status}</p>
      <button onClick={active || call.current || starting ? stop : start}>{active ? 'End call' : starting ? 'Cancel connection' : 'Start voice call'}</button>
      <p className="note">Microphone stays on during replies. Headphones help reduce echo.</p>
      {active && <div className="call-controls"><span className="badge">{callState}</span><button className="secondary" onClick={() => control({type:'call_state', state:callState === 'hold' ? 'connected' : 'hold'})} disabled={!['connected','hold'].includes(callState)}>{callState === 'hold' ? 'Resume' : 'Hold'}</button><button className="secondary" onClick={() => control({type:'call_state',state:'transfer'})} disabled={!['connected','hold'].includes(callState)}>Simulate transfer</button>{workflowMode === 'connect' && <><button className="secondary" onClick={() => control({type:'dtmf',digit:'1'})}>Press 1 · confirm</button><button className="secondary" onClick={() => control({type:'dtmf',digit:'0'})}>Press 0 · decline</button></>}</div>}
      {workflowMode === 'connect' && <p className="note">Willowmere Demo Clinic is fictional. Try “Book Monday at three PM with Doctor Lee.” Wait for the full read-back, then say yes. Weekdays within 30 days; 9, 10, 11 AM or 2, 3, 4 PM. One call at a time.</p>}
      {error && <p role="alert">{error}</p>}
    </section>
    <section className="details"><div><h2>Conversation</h2><div className="transcript" ref={transcript} aria-live="polite">{lines.length ? lines.map((line, i) => <p key={i}><strong>{line.role}</strong>{line.text}</p>) : <p className="placeholder">Your conversation will appear here. Assistant text appears after its audio finishes playing.</p>}</div></div>
      <div><h2>Latest turn</h2><dl>{[['VAD endpoint', 'vad_endpoint_ms'], ['ASR', 'asr_ms'], ['LLM first token', 'llm_ttft_ms'], ['TTS first chunk', 'tts_first_chunk_ms'], ['Server first audio¹', 'server_voice_to_voice_ms']].map(([label,key]) => <React.Fragment key={key}><dt>{label}</dt><dd>{metrics[key] === undefined ? '—' : `${Math.round(metrics[key])} ms`}</dd></React.Fragment>)}</dl><p className="note">¹ From last VAD speech frame to first server audio chunk. Browser output buffering is additional.</p></div>
    </section>
    {workflowMode === 'connect' && <section className="booking-panel"><h2>Verified appointment workflow</h2><p className="workflow-steps">Collect → Confirm → Commit → Report</p><p>State: <strong>{booking?.state ?? 'collect'}</strong></p>{booking?.draft?.date && <p>{booking.state === 'report' ? 'Verified details' : 'Pending'}: {booking.draft.date} · {booking.draft.time} · Doctor {booking.draft.clinician}</p>}<p className="note">No write until the spoken read-back has finished and you confirm. Caller access uses this browser's local session, not real-world identity verification.</p>{bookings.length ? bookings.map(row => <div className="booking-row" key={row.id}><strong>{row.status}</strong><span>{row.date} · {row.time} · Doctor {row.clinician}</span><small>Reference {row.id}</small></div>) : <p className="placeholder">No appointments for this local caller.</p>}</section>}
    {turns.length > 0 && <section className="turn-traces"><h2>Per-turn stage traces</h2><div className="turn-buttons">{turns.map(turn => <button className="secondary" key={turn.id} onClick={() => setMetrics(turn.metrics)}>Turn {turn.id}</button>)}</div>{[['ASR','asr_ms'],['LLM generation','llm_ms'],['Verified tools','tools_ms'],['TTS first chunk','tts_first_chunk_ms']].map(([label,key]) => metrics[key] != null && <div className="timing-row" key={key}><span>{label}</span><meter min="0" max={Math.max(...Object.values(metrics).filter(Number.isFinite), 1)} value={metrics[key]}/><strong>{Math.round(metrics[key])} ms</strong></div>)}<p className="note">Stage durations can overlap. They are independent timings, not a summed latency estimate. Local OpenTelemetry spans contain no transcript or audio.</p></section>}
    <Review active={active || starting}/>
    <footer>Local models · SQLite verification · no cloud inference. Live audio and transcripts stay in memory. Replay files contain synthetic evaluation audio only.</footer>
  </main>;
}

createRoot(document.getElementById('root')!).render(<App/>);
