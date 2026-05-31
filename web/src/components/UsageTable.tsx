import { useMemo } from "react";

type UsageItem = {
  id?: string;
  requestId?: string;
  transport?: string;
  audioSeconds?: number;
  costUsd?: number;
  occurredAt?: string;
};

type AggregatedRow = {
  key: string;
  requestId: string;
  transport: string;
  audioSeconds: number;
  costUsd: number;
  occurredAt: string;
  chunks: number;
};

type Props = {
  items: UsageItem[];
  total?: number;
  loading?: boolean;
};

function aggregateByRequest(items: UsageItem[]): AggregatedRow[] {
  const map = new Map<string, AggregatedRow>();

  for (const e of items) {
    const requestId = e.requestId ?? e.id ?? "unknown";
    const transport = e.transport ?? "—";
    const key = `${requestId}:${transport}`;
    const at = e.occurredAt ?? "";
    const audio = e.audioSeconds ?? 0;
    const cost = e.costUsd ?? 0;

    const existing = map.get(key);
    if (!existing) {
      map.set(key, {
        key,
        requestId,
        transport,
        audioSeconds: audio,
        costUsd: cost,
        occurredAt: at,
        chunks: 1,
      });
      continue;
    }
    existing.audioSeconds += audio;
    existing.costUsd += cost;
    existing.chunks += 1;
    if (at && (!existing.occurredAt || at > existing.occurredAt)) {
      existing.occurredAt = at;
    }
  }

  return [...map.values()].sort((a, b) =>
    (b.occurredAt || "").localeCompare(a.occurredAt || "")
  );
}

function shortId(id: string): string {
  if (id.length <= 12) return id;
  return `${id.slice(0, 8)}…`;
}

export function UsageTable({ items, total, loading }: Props) {
  const rows = useMemo(() => aggregateByRequest(items), [items]);

  if (loading) {
    return <p className="muted">Loading usage…</p>;
  }
  if (!items.length) {
    return <p className="muted">No usage events yet.</p>;
  }

  return (
    <>
      <p className="muted">
        {rows.length} request(s)
        {total != null && total > rows.length
          ? ` · ${total} billing chunk(s) rolled up`
          : rows.some((r) => r.chunks > 1)
            ? " · grouped by request"
            : ""}
      </p>
      <div style={{ overflowX: "auto" }}>
        <table className="usage-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Mode</th>
              <th>Request</th>
              <th>Audio (s)</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} title={r.requestId}>
                <td>
                  {r.occurredAt
                    ? new Date(r.occurredAt).toLocaleString()
                    : "—"}
                </td>
                <td>{r.transport}</td>
                <td className="mono">
                  {shortId(r.requestId)}
                  {r.chunks > 1 ? (
                    <span className="muted"> ({r.chunks})</span>
                  ) : null}
                </td>
                <td>{r.audioSeconds.toFixed(2)}</td>
                <td>${r.costUsd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
