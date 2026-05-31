/**
 * PCM playback for TTS: gapless streaming via AudioWorklet, or one-shot full-buffer play.
 */

const SAMPLE_RATE = 24000;
const RING_SECONDS = 45;
const DEFAULT_START_BUFFER_SEC = 0.25;

const WORKLET_SRC = `
class PCMRingPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    const cap = ${SAMPLE_RATE} * ${RING_SECONDS};
    this.ring = new Float32Array(cap);
    this.w = 0;
    this.r = 0;
    this.available = 0;
    this.playing = false;
    this.port.onmessage = (e) => {
      if (e.data.type === "start") {
        this.playing = true;
        return;
      }
      if (e.data.type === "stop") {
        this.playing = false;
        this.available = 0;
        this.r = 0;
        this.w = 0;
        return;
      }
      const pcm = new Uint8Array(e.data.buf);
      const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
      const n = pcm.byteLength >> 1;
      for (let i = 0; i < n; i++) {
        const s = view.getInt16(i << 1, true) / 32768;
        this.ring[this.w] = s;
        this.w = (this.w + 1) % this.ring.length;
        if (this.available < this.ring.length) {
          this.available++;
        } else {
          this.r = (this.r + 1) % this.ring.length;
        }
      }
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!this.playing) {
      out.fill(0);
      return true;
    }
    for (let i = 0; i < out.length; i++) {
      if (this.available > 0) {
        out[i] = this.ring[this.r];
        this.r = (this.r + 1) % this.ring.length;
        this.available--;
      } else {
        out[i] = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-ring-player", PCMRingPlayer);
`;

let workletModuleURL: string | null = null;

function workletModule(): string {
  if (!workletModuleURL) {
    workletModuleURL = URL.createObjectURL(
      new Blob([WORKLET_SRC], { type: "application/javascript" })
    );
  }
  return workletModuleURL;
}

function pcmToFloat32(pcm: Uint8Array): Float32Array {
  const samples = pcm.length >> 1;
  const out = new Float32Array(samples);
  const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  for (let i = 0; i < samples; i++) {
    out[i] = view.getInt16(i << 1, true) / 32768;
  }
  return out;
}

export function pcmAudioSeconds(pcm: Uint8Array, sampleRate = SAMPLE_RATE): number {
  return pcm.length / 2 / sampleRate;
}

export class PCMStreamPlayer {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private initPromise: Promise<void> | null = null;
  private samplesQueued = 0;
  private playbackStarted = false;
  private readonly startBufferSamples: number;
  private oneShotSource: AudioBufferSourceNode | null = null;

  constructor(startBufferSec = DEFAULT_START_BUFFER_SEC) {
    this.startBufferSamples = Math.floor(SAMPLE_RATE * startBufferSec);
  }

  private async ensureReady(): Promise<AudioContext> {
    if (this.initPromise) await this.initPromise;
    if (!this.ctx) {
      this.initPromise = (async () => {
        const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
        if (ctx.state === "suspended") await ctx.resume();
        await ctx.audioWorklet.addModule(workletModule());
        const node = new AudioWorkletNode(ctx, "pcm-ring-player", {
          outputChannelCount: [1],
        });
        node.connect(ctx.destination);
        this.ctx = ctx;
        this.node = node;
      })();
      await this.initPromise;
    }
    return this.ctx!;
  }

  private maybeStartPlayback() {
    if (this.playbackStarted || !this.node) return;
    if (this.samplesQueued < this.startBufferSamples) return;
    this.node.port.postMessage({ type: "start" });
    this.playbackStarted = true;
  }

  /** Stream mode: enqueue chunks; plays after a short prebuffer. */
  enqueue(pcm: Uint8Array, _sampleRate = SAMPLE_RATE) {
    if (pcm.length < 2) return;
    void this.ensureReady().then(() => {
      const copy = new Uint8Array(pcm);
      this.samplesQueued += copy.length >> 1;
      this.node.port.postMessage({ buf: copy.buffer }, [copy.buffer]);
      this.maybeStartPlayback();
    });
  }

  flush() {
    if (!this.playbackStarted && this.node && this.samplesQueued > 0) {
      this.node.port.postMessage({ type: "start" });
      this.playbackStarted = true;
    }
  }

  /** Play full PCM in one buffer (no chunk-boundary artifacts). */
  async playAll(pcm: Uint8Array, sampleRate = SAMPLE_RATE): Promise<void> {
    if (pcm.length < 2) return;
    await this.stop();
    const ctx = await this.ensureReady();
    const floats = pcmToFloat32(pcm);
    const buffer = ctx.createBuffer(1, floats.length, sampleRate);
    buffer.copyToChannel(floats, 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    this.oneShotSource = source;
    return new Promise((resolve, reject) => {
      source.onended = () => {
        this.oneShotSource = null;
        resolve();
      };
      source.onerror = () => reject(new Error("playback error"));
      source.start();
    });
  }

  async stop() {
    this.oneShotSource?.stop();
    this.oneShotSource = null;
    if (this.node) {
      this.node.port.postMessage({ type: "stop" });
      this.node.disconnect();
      this.node = null;
    }
    if (this.ctx) {
      await this.ctx.close();
      this.ctx = null;
    }
    this.initPromise = null;
    this.samplesQueued = 0;
    this.playbackStarted = false;
  }
}
