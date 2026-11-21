// The browser resamples the microphone through a 16 kHz AudioContext.
// A worklet keeps capture off the UI thread; always send 512 PCM16 samples.
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.frame = new Int16Array(512); this.offset = 0; }
  process(inputs, outputs) {
    // An inactive input bus can have no channels. Keep the sample clock moving
    // with the zeroed output bus so the server still receives endpoint silence.
    const audio = inputs[0]?.[0] ?? outputs[0]?.[0];
    if (!audio) return true;
    for (const sample of audio) {
      const clamped = Math.max(-1, Math.min(1, sample));
      this.frame[this.offset++] = Math.round(clamped * (clamped < 0 ? 32768 : 32767));
      if (this.offset === 512) {
        this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
        this.frame = new Int16Array(512); this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('eywa-capture', Capture);
