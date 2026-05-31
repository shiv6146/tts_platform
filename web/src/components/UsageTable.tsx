type UsageItem = {
  id?: string;
  requestId?: string;
  transport?: string;
  audioSeconds?: number;
  costUsd?: number;
  occurredAt?: string;
};

type Props = {
  items: UsageItem[];
  total?: number;
  loading?: boolean;
};

export function UsageTable({ items, total, loading }: Props) {
  if (loading) {
    return <p className="muted">Loading usage…</p>;
  }
  if (!items.length) {
    return <p className="muted">No usage events yet.</p>;
  }
  return (
    <>
      <p className="muted">{total ?? items.length} event(s)</p>
      <div style={{ overflowX: "auto" }}>
        <table className="usage-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Mode</th>
              <th>Audio (s)</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id ?? e.requestId}>
                <td>
                  {e.occurredAt
                    ? new Date(e.occurredAt).toLocaleString()
                    : "—"}
                </td>
                <td>{e.transport ?? "—"}</td>
                <td>{(e.audioSeconds ?? 0).toFixed(2)}</td>
                <td>${(e.costUsd ?? 0).toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
