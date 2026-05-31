type Voice = { id?: string; label?: string; hint?: string };

type Props = {
  voices: Voice[];
  value: string;
  onChange: (id: string) => void;
};

export function VoicePicker({ voices, value, onChange }: Props) {
  return (
    <div>
      <label htmlFor="voice-select">Voice</label>
      <select
        id="voice-select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Select a voice…</option>
        {voices.map((v) => (
          <option key={v.id} value={v.id ?? ""}>
            {v.label ?? v.id}
          </option>
        ))}
      </select>
      {value && (
        <p className="muted" style={{ marginTop: "0.35rem" }}>
          {voices.find((v) => v.id === value)?.hint}
        </p>
      )}
    </div>
  );
}
