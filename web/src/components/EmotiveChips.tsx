type Tag = { tag?: string; label?: string };

type Props = {
  tags: Tag[];
  onInsert: (tag: string) => void;
};

export function EmotiveChips({ tags, onInsert }: Props) {
  return (
    <div>
      <label>Expression</label>
      <div className="chips">
        {tags.map((t) => (
          <button
            key={t.tag}
            type="button"
            className="chip"
            onClick={() => t.tag && onInsert(t.tag)}
          >
            {t.label ?? t.tag}
          </button>
        ))}
      </div>
      <p className="muted">
        Tags like &lt;laugh&gt; and &lt;sigh&gt; are inserted into your text.
      </p>
    </div>
  );
}
