/**
 * Gapless PCM playback via AudioWorklet ring buffer.
 * Waits for a minimum buffer before playback so slow chunk delivery does not
 * cause underrun silence (SNAC yields ~85ms audio every ~200–300ms while generating).
 */

const SAMPLE_RATE = 24000;
const RING_SECONDS = 45;
/** Audio to buffer before starting speakers (covers generation cadence). */
const DEFAULT_START_BUFFER_SEC = 0.45;

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

export class PCMStreamPlayer {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private initPromise: Promise<void> | null = null;
  private samplesQueued = 0;
  private playbackStarted = false;
  private readonly startBufferSamples: number;

  constructor(startBufferSec = DEFAULT_START_BUFFER_SEC) {
    this.startBufferSamples = Math.floor(SAMPLE_RATE * startBufferSec);
  }

  private ensureReady(): Promise<void> {
    if (this.initPromise) return this.initPromise;
    this.initPromise = (async () => {
      const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      if (ctx.state === "suspended") {
        await ctx.resume();
      }
      await ctx.audioWorklet.addModule(workletModule());
      const node = new AudioWorkletNode(ctx, "pcm-ring-player", {
        outputChannelCount: [1],
      });
      node.connect(ctx.destination);
      this.ctx = ctx;
      this.node = node;
    })();
    return this.initPromise;
  }

  private maybeStartPlayback() {
    if (this.playbackStarted || !this.node) return;
    if (this.samplesQueued < this.startBufferSamples) return;
    this.node.port.postMessage({ type: "start" });
    this.playbackStarted = true;
  }

  enqueue(pcm: Uint8Array, _sampleRate = SAMPLE_RATE) {
    if (pcm.length < 2) return;
    void this.ensureReady().then(() => {
      const copy = new Uint8Array(pcm);
      this.samplesQueued += copy.length >> 1;
      this.node?.port.postMessage({ buf: copy.buffer }, [copy.buffer]);
      this.maybeStartPlayback();
    });
  }

  /** Start playback with whatever is buffered (end of stream / short phrase). */
  flush() {
    if (!this.playbackStarted && this.node && this.samplesQueued > 0) {
      this.node.port.postMessage({ type: "start" });
      this.playbackStarted = true;
    }
  }

  async stop() {
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
