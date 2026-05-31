type Props = {
  balanceUsd?: number;
  pricePerMinuteUsd?: number;
  loading?: boolean;
};

export function WalletCard({ balanceUsd, pricePerMinuteUsd, loading }: Props) {
  return (
    <div className="card wallet-strip">
      <div>
        <div className="muted">Wallet balance</div>
        <strong>{loading ? "…" : `$${(balanceUsd ?? 0).toFixed(4)}`}</strong>
      </div>
      <div className="muted" style={{ textAlign: "right" }}>
        ${(pricePerMinuteUsd ?? 0).toFixed(4)}
        <br />
        per audio min
      </div>
    </div>
  );
}
