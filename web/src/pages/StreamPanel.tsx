import { useRef, useState } from "react";
import { getApiKey } from "../lib/auth";
import { PCMStreamPlayer, pcmAudioSeconds } from "../audio/pcmPlayer";
import { EmotiveChips } from "../components/EmotiveChips";
import type { VoicesMeta } from "../api/client";

type Props = {
  meta: VoicesMeta | null;
  voice: string;
  text: string;
  onTextChange: (t: string) => void;
  onDone: () => void;
};

export function StreamPanel({ meta, voice, text, onTextChange, onDone }: Props) {
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [bytesReceived, setBytesReceived] = useState(0);
  const playerRef = useRef<PCMStreamPlayer | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  function insertTag(tag: string) {
    const el = textareaRef.current;
    if (!el) {
      onTextChange(text + tag);
      return;
    }
    const start = el.selectionStart;
    const end = el.selectionEnd;
    onTextChange(text.slice(0, start) + tag + text.slice(end));
  }

  function stop() {
    abortRef.current?.abort();
    void playerRef.current?.stop();
    playerRef.current = null;
    setLoading(false);
    setBytesReceived(0);
    setStatus("Stopped");
  }

  async function stream() {
    if (!voice || !text.trim()) return;
    stop();
    const player = new PCMStreamPlayer(0.2);
    playerRef.current = player;
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setBytesReceived(0);
    setStatus("Connecting…");
    let totalBytes = 0;
    try {
      const res = await fetch("/v1/tts/stream", {
        method: "POST",
        credentials: "include",
        signal: ac.signal,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getApiKey() ?? ""}`,
        },
        body: JSON.stringify({ text, voice }),
      });
      if (!res.ok) {
        setStatus(`Error ${res.status}`);
        setLoading(false);
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) {
        setStatus("No response body");
        setLoading(false);
        return;
      }
      setStatus("Streaming…");
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value?.length) {
          totalBytes += value.length;
          setBytesReceived(totalBytes);
          player.enqueue(new Uint8Array(value));
          const sec = pcmAudioSeconds(
            new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
          );
          if (totalBytes === value.length) {
            setStatus(`First bytes (${sec > 0 ? `${(sec * 1000).toFixed(0)}ms audio` : value.length + " B"})…`);
          } else {
            setStatus(`Streaming… ${(totalBytes / 1024).toFixed(1)} KB received`);
          }
        }
      }
      player.flush();
      const totalSec = totalBytes / (24000 * 2);
      setStatus(`Done (${totalSec.toFixed(2)}s audio, ${(totalBytes / 1024).toFixed(1)} KB)`);
      onDone();
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setStatus("Stream failed");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h2>Stream</h2>
      <p className="muted">
        Chunked PCM over HTTP — audio plays as chunks arrive (24 kHz).
      </p>
      {!voice && <p className="status err">Select a voice in Voice Lab first.</p>}
      <div style={{ marginTop: "0.75rem" }}>
        <label htmlFor="stream-text">Text</label>
        <textarea
          id="stream-text"
          ref={textareaRef}
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          disabled={!voice}
        />
      </div>
      <EmotiveChips tags={meta?.emotiveTags ?? []} onInsert={insertTag} />
      <div className="row">
        <button type="button" onClick={stream} disabled={loading || !voice}>
          {loading ? "Playing…" : "Stream speak"}
        </button>
        <button type="button" className="secondary" onClick={stop}>
          Stop
        </button>
      </div>
      {status && <p className="status">{status}</p>}
      {loading && bytesReceived > 0 && (
        <p className="muted">Received {bytesReceived.toLocaleString()} bytes</p>
      )}
    </div>
  );
}
