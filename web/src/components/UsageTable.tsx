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

function eventRequestId(e: UsageItem): string | undefined {
  const raw = e.requestId ?? (e as { request_id?: string }).request_id;
  if (typeof raw !== "string") return undefined;
  const t = raw.trim();
  return t.length > 0 ? t : undefined;
}

function aggregateByRequest(items: UsageItem[]): AggregatedRow[] {
  const map = new Map<string, AggregatedRow>();

  for (const e of items) {
    const requestId = eventRequestId(e);
    const transport = e.transport ?? "—";
    const key = requestId ? `${requestId}:${transport}` : `event:${e.id ?? Math.random()}`;
    const at = e.occurredAt ?? "";
    const audio = Number(e.audioSeconds) || 0;
    const cost = Number(e.costUsd) || 0;

    const existing = map.get(key);
    if (!existing) {
      map.set(key, {
        key,
        requestId: requestId ?? (e.id ?? "—"),
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
  if (id.length <= 14) return id;
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

export function UsageTable({ items, total, loading }: Props) {
  const rows = useMemo(() => aggregateByRequest(items), [items]);
  const rawChunks = items.length;

  if (loading) {
    return <p className="muted">Loading usage…</p>;
  }
  if (!items.length) {
    return <p className="muted">No usage events yet.</p>;
  }

  return (
    <>
      <p className="muted">
        {rows.length} request(s) · {rawChunks} billing event(s)
        {total != null && total > rawChunks ? ` · ${total} total in DB` : ""}
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
                    <span className="muted"> ×{r.chunks}</span>
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
