// Synthetic caller device for benchmarks only. Inference and app code stay real.
(() => {
  const state = window.eywaProbe = {frames: [], events: [], outputStarts: [], stops: [], stimulus: []};
  const turns = new WeakMap();
  const NativeWS = window.WebSocket;
  window.WebSocket = class extends NativeWS {
    constructor(...args) {
      super(...args);
      this.addEventListener('message', message => {
        const e = JSON.parse(message.data);
        if (e.type === 'audio') {
          state.currentTurn = e.turn_id;
          state.events.push({type: 'audio', t: performance.now(), turn_id: e.turn_id});
        }
        else if (['response_done', 'interrupted', 'speech_started', 'error'].includes(e.type)) {
          state.events.push({...e, t: performance.now()});
        }
      });
    }
    send(data) {
      if (data instanceof ArrayBuffer) {
        const samples = new Int16Array(data);
        const rms = Math.sqrt(samples.reduce((s, x) => s + (x / 32768) ** 2, 0) / samples.length);
        state.frames.push({t: performance.now(), rms});
      } else {
        const e = JSON.parse(data);
        state.events.push({...e, t: performance.now(), direction: 'client'});
      }
      return super.send(data);
    }
  };
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function(when, ...args) {
    if (this.buffer?.sampleRate === 22050) {
      turns.set(this, state.currentTurn);
      state.outputStarts.push({t: performance.now() + Math.max(0, (when || 0) - this.context.currentTime) * 1000,
                              duration_ms: this.buffer.duration * 1000, turn_id: state.currentTurn});
    }
    return start.call(this, when, ...args);
  };
  const stop = AudioBufferSourceNode.prototype.stop;
  AudioBufferSourceNode.prototype.stop = function(...args) {
    if (this.buffer?.sampleRate === 22050) state.stops.push({t: performance.now(), turn_id: turns.get(this)});
    return stop.apply(this, args);
  };
  navigator.mediaDevices.getUserMedia = async constraints => {
    state.requestedEchoCancellation = constraints.audio.echoCancellation;
    const context = new AudioContext({sampleRate: 16000});
    const destination = context.createMediaStreamDestination();
    // A physical capture device supplies a continuous clock, including silence.
    const keepalive = context.createOscillator();
    const silent = context.createGain(); silent.gain.value = 0;
    keepalive.connect(silent).connect(destination); keepalive.start();
    Object.defineProperty(state, 'inputState', {get: () => context.state, enumerable: true});
    await context.resume();
    window.eywaPlayFixture = async base64 => {
      const raw = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
      const view = new DataView(raw.buffer);
      const buffer = context.createBuffer(1, raw.length / 2, 16000);
      const samples = buffer.getChannelData(0);
      for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
      const source = context.createBufferSource(); source.buffer = buffer; source.connect(destination);
      const t = performance.now(); state.stimulus.push({t, frame: state.frames.length});
      source.start();
    };
    return destination.stream;
  };
})();
