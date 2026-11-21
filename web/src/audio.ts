export class Playback {
  sources = new Set<AudioBufferSourceNode>();
  last = new Map<number, AudioBufferSourceNode>();
  ends = new Map<number, number>();
  next = 0;
  version = 0;
  constructor(public context: AudioContext) {}

  push(pcm: string, rate: number, clause: number) {
    const raw = atob(pcm);
    const bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
    const view = new DataView(bytes.buffer);
    const buffer = this.context.createBuffer(1, bytes.length / 2, rate);
    const samples = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    const at = Math.max(this.context.currentTime + 0.01, this.next);
    this.next = at + buffer.duration;
    this.sources.add(source); this.last.set(clause, source); this.ends.set(clause, this.next);
    source.onended = () => { this.sources.delete(source); source.disconnect(); };
    source.start(at);
  }

  endClause(clause: number, played: () => void) {
    const source = this.last.get(clause);
    const version = this.version;
    const done = () => {
      this.last.delete(clause); this.ends.delete(clause);
      if (version === this.version) played();
    };
    if (!source) return; // Never acknowledge text for which no audio was received.
    if (!this.sources.has(source)) { done(); return; }
    source.onended = () => {
      this.sources.delete(source); source.disconnect(); done();
    };
  }

  stop() {
    this.version++;
    for (const source of this.sources) { source.onended = null; source.stop(); source.disconnect(); }
    this.sources.clear(); this.last.clear(); this.ends.clear();
    this.next = this.context.currentTime;
  }
}
