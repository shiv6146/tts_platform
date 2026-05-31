import { useRef, useState } from "react";
import { api } from "../api/client";
import type { VoicesMeta } from "../api/client";
import { EmotiveChips } from "../components/EmotiveChips";
import { VoicePicker } from "../components/VoicePicker";

type Props = {
  meta: VoicesMeta | null;
  voice: string;
  onVoiceChange: (v: string) => void;
  onDone: () => void;
};

export function VoiceLabPanel({ meta, voice, onVoiceChange, onDone }: Props) {
  const [text, setText] = useState(
    "Hello <laugh> this is a voice test from the Orpheus TTS platform."
  );
  const [status, setStatus] = useState("");
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function insertTag(tag: string) {
    const el = textareaRef.current;
    if (!el) {
      setText((t) => t + tag);
      return;
    }
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const next = text.slice(0, start) + tag + text.slice(end);
    setText(next);
    requestAnimationFrame(() => {
      el.focus();
      const pos = start + tag.length;
      el.setSelectionRange(pos, pos);
    });
  }

  async function generate() {
    if (!voice || !text.trim()) return;
    setLoading(true);
    setStatus("Submitting job…");
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
      setAudioUrl(null);
    }
    const { data, error, response } = await api.POST("/v1/tts/async", {
      body: { text, voice },
    });
    if (!response.ok || error || !data?.jobId) {
      setStatus("Failed to start job");
      setLoading(false);
      return;
    }
    const jobId = data.jobId;
    let attempts = 0;
    const poll = async () => {
      attempts++;
      const res = await api.GET("/v1/tts/async/{jobId}", {
        params: { path: { jobId } },
      });
      const st = res.data?.status;
      setStatus(`Job: ${st ?? "unknown"}`);
      if (st === "completed") {
        const audioRes = await fetch(`/v1/tts/async/${jobId}/audio`, {
          credentials: "include",
          headers: { Authorization: `Bearer ${sessionStorage.getItem("tts_apiKey") ?? ""}` },
        });
        if (audioRes.ok) {
          const blob = await audioRes.blob();
          setAudioUrl(URL.createObjectURL(blob));
          setStatus("Ready — WAV loaded");
          onDone();
        } else {
          setStatus("Failed to load audio");
        }
        setLoading(false);
        return;
      }
      if (st === "failed") {
        setStatus(res.data?.error ?? "Job failed");
        setLoading(false);
        return;
      }
      if (attempts > 120) {
        setStatus("Timed out");
        setLoading(false);
        return;
      }
      setTimeout(poll, 1000);
    };
    void poll();
  }

  return (
    <div className="card">
      <h2>Voice Lab</h2>
      <p className="muted">Pick a voice, compose text with expression tags, generate WAV.</p>
      <VoicePicker
        voices={meta?.voices ?? []}
        value={voice}
        onChange={onVoiceChange}
      />
      {voice && (
        <>
          <div style={{ marginTop: "1rem" }}>
            <label htmlFor="tts-text">Text</label>
            <textarea
              id="tts-text"
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <EmotiveChips tags={meta?.emotiveTags ?? []} onInsert={insertTag} />
          <div className="row">
            <button type="button" onClick={generate} disabled={loading || !text.trim()}>
              {loading ? "Generating…" : "Generate WAV"}
            </button>
          </div>
          {status && (
            <p className={`status ${status.includes("Failed") ? "err" : "ok"}`}>{status}</p>
          )}
          {audioUrl && (
            <>
              <audio controls src={audioUrl} />
              <div className="row">
                <a
                  href={audioUrl}
                  download="tts-output.wav"
                  style={{
                    flex: 1,
                    textAlign: "center",
                    padding: "0.65rem",
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    color: "var(--text)",
                    textDecoration: "none",
                    display: "block",
                  }}
                >
                  Download WAV
                </a>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
