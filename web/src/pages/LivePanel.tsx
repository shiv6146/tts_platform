import { useCallback, useEffect, useRef, useState } from "react";
import { PCMStreamPlayer } from "../audio/pcmPlayer";

type Props = {
  voice: string;
  onDone: () => void;
};

type ControlMsg = {
  type: string;
  message?: string;
  error?: string;
  ttfb_ms?: number;
  delivered_audio_seconds?: number;
};

const DEBOUNCE_MS = 400;

export function LivePanel({ voice, onDone }: Props) {
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const playerRef = useRef<PCMStreamPlayer | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sentIndexRef = useRef(0);
  const inputRef = useRef(input);
  const voiceRef = useRef(voice);

  inputRef.current = input;

  useEffect(() => {
    voiceRef.current = voice;
  }, [voice]);

  const appendLog = useCallback((line: string) => {
    setLog((prev) => [...prev.slice(-20), line]);
  }, []);

  const sendPhrase = useCallback((text: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !text.trim()) return;
    ws.send(
      JSON.stringify({
        type: "text",
        text,
        final: true,
        voice: voiceRef.current || "tara",
      })
    );
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    playerRef.current?.stop();
    playerRef.current = new PCMStreamPlayer(0.35);
    sentIndexRef.current = 0;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/v1/tts/live`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      appendLog("Connected");
    };
    ws.onclose = (ev) => {
      setConnected(false);
      if (ev.code !== 1000 && ev.reason) {
        appendLog(`Disconnected: ${ev.reason}`);
      } else {
        appendLog("Disconnected");
      }
    };
    ws.onerror = () => appendLog("WebSocket error");
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try {
          const msg = JSON.parse(ev.data) as ControlMsg;
          if (msg.type === "ready") appendLog("Ready");
          if (msg.type === "metadata" && msg.ttfb_ms != null) {
            appendLog(`First audio: ${msg.ttfb_ms} ms`);
          }
          if (msg.type === "insufficient_balance") {
            appendLog(msg.message ?? "Insufficient balance");
          }
          if (msg.type === "error") appendLog(msg.error ?? "Error");
          if (msg.type === "done") {
            appendLog(
              `Done (${(msg.delivered_audio_seconds ?? 0).toFixed(2)} s audio)`
            );
            onDone();
          }
        } catch {
          /* ignore */
        }
        return;
      }
      playerRef.current?.enqueue(new Uint8Array(ev.data as ArrayBuffer));
    };
  }, [appendLog, onDone]);

  const disconnect = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const pending = inputRef.current.slice(sentIndexRef.current).trim();
    if (pending) sendPhrase(pending);
    wsRef.current?.close(1000);
    wsRef.current = null;
    playerRef.current?.stop();
    playerRef.current = null;
    setConnected(false);
  }, [sendPhrase]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      wsRef.current?.close(1000);
      wsRef.current = null;
      playerRef.current?.stop();
      playerRef.current = null;
    };
  }, []);

  function handleChange(value: string) {
    setInput(value);
    if (!connected) return;

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const pending = value.slice(sentIndexRef.current).trim();
      if (!pending) return;
      sendPhrase(pending);
      sentIndexRef.current = value.length;
    }, DEBOUNCE_MS);

    if (/[.!?]\s*$/.test(value)) {
      const pending = value.slice(sentIndexRef.current).trim();
      if (pending) {
        sendPhrase(pending);
        sentIndexRef.current = value.length;
        if (debounceRef.current) clearTimeout(debounceRef.current);
      }
    }
  }

  return (
    <div className="card">
      <h2>Live</h2>
      <p className="muted">
        Type naturally — phrases send after a pause and play as audio arrives.
      </p>
      {!voice && <p className="status err">Select a voice in Voice Lab first.</p>}
      <div className="row">
        {!connected ? (
          <button type="button" onClick={connect} disabled={!voice}>
            Connect
          </button>
        ) : (
          <button type="button" className="danger" onClick={disconnect}>
            Disconnect
          </button>
        )}
      </div>
      <div style={{ marginTop: "0.75rem" }}>
        <label htmlFor="live-input">Message</label>
        <textarea
          id="live-input"
          value={input}
          onChange={(e) => handleChange(e.target.value)}
          disabled={!connected}
          placeholder="Type here — audio plays as you pause…"
          rows={5}
        />
      </div>
      <div className="live-log">
        {log.map((l, i) => (
          <div key={i}>{l}</div>
        ))}
      </div>
    </div>
  );
}
