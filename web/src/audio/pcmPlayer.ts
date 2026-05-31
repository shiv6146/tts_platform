const SAMPLE_RATE = 24000;

/** Minimum buffered audio before playback starts (reduces underrun gaps between chunks). */
const PREBUFFER_MS = 120;

function mergeChunks(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.length;
  }
  return out;
}

export class PCMStreamPlayer {
  private ctx: AudioContext | null = null;
  private nextTime = 0;
  private queue: Uint8Array[] = [];
  private queuedSamples = 0;
  private playing = false;
  private readonly prebufferSamples: number;

  constructor(prebufferMs = PREBUFFER_MS) {
    this.prebufferSamples = Math.max(
      1,
      Math.floor((SAMPLE_RATE * prebufferMs) / 1000)
    );
  }

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
    if (pcm.length < 2) return;
    this.queue.push(pcm);
    this.queuedSamples += pcm.length / 2;

    if (!this.playing) {
      if (this.queuedSamples < this.prebufferSamples) return;
      this.playing = true;
      const ctx = this.ensureContext();
      this.nextTime = ctx.currentTime + 0.02;
    }

    this.drainQueue(sampleRate);
  }

  /** Play any audio still queued (call when the stream ends). */
  flush(sampleRate = SAMPLE_RATE) {
    if (this.queuedSamples === 0) return;
    if (!this.playing) {
      this.playing = true;
      const ctx = this.ensureContext();
      this.nextTime = ctx.currentTime + 0.02;
    }
    this.drainQueue(sampleRate);
  }

  private drainQueue(sampleRate: number) {
    while (this.queue.length > 0) {
      const pcm = mergeChunks(this.queue);
      this.queue = [];
      this.queuedSamples = 0;
      this.scheduleBuffer(pcm, sampleRate);
    }
  }

  private scheduleBuffer(pcm: Uint8Array, sampleRate: number) {
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

    if (this.nextTime < ctx.currentTime) {
      this.nextTime = ctx.currentTime;
    }
    source.start(this.nextTime);
    this.nextTime += buffer.duration;
  }

  stop() {
    this.queue = [];
    this.queuedSamples = 0;
    this.playing = false;
    if (this.ctx) {
      void this.ctx.close();
      this.ctx = null;
    }
    this.nextTime = 0;
  }
}
