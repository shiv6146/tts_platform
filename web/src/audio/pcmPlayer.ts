const SAMPLE_RATE = 24000;

export class PCMStreamPlayer {
  private ctx: AudioContext | null = null;
  private nextTime = 0;

  private ensureContext(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      this.nextTime = this.ctx.currentTime;
    }
    if (this.ctx.state === "suspended") {
      void this.ctx.resume();
    }
    return this.ctx;
  }

  enqueue(pcm: Uint8Array, sampleRate = SAMPLE_RATE) {
    const ctx = this.ensureContext();
    const samples = pcm.length / 2;
    if (samples === 0) return;
    const buffer = ctx.createBuffer(1, samples, sampleRate);
    const channel = buffer.getChannelData(0);
    const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    for (let i = 0; i < samples; i++) {
      channel[i] = view.getInt16(i * 2, true) / 32768;
    }
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    const start = Math.max(this.nextTime, ctx.currentTime);
    source.start(start);
    this.nextTime = start + buffer.duration;
  }

  stop() {
    if (this.ctx) {
      void this.ctx.close();
      this.ctx = null;
    }
    this.nextTime = 0;
  }
}
