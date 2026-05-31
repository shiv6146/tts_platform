import { useCallback, useEffect, useState } from "react";
import { api, logout, type VoicesMeta } from "../api/client";
import { UsageTable } from "../components/UsageTable";
import { WalletCard } from "../components/WalletCard";
import { getUsername } from "../lib/auth";
import { LivePanel } from "./LivePanel";
import { StreamPanel } from "./StreamPanel";
import { VoiceLabPanel } from "./VoiceLabPanel";

type Tab = "voice" | "stream" | "live" | "usage";
type MainTab = "studio" | "usage";

type Props = {
  onLogout: () => void;
};

export function StudioPage({ onLogout }: Props) {
  const [mainTab, setMainTab] = useState<MainTab>("studio");
  const [tab, setTab] = useState<Tab>("voice");
  const [meta, setMeta] = useState<VoicesMeta | null>(null);
  const [voice, setVoice] = useState("");
  const [text, setText] = useState(
    "Hello <laugh> this is a streaming test from the Orpheus TTS platform."
  );
  const [balanceUsd, setBalanceUsd] = useState<number>();
  const [pricePerMinuteUsd, setPricePerMinuteUsd] = useState<number>();
  const [usageItems, setUsageItems] = useState<
    {
      id?: string;
      requestId?: string;
      transport?: string;
      audioSeconds?: number;
      costUsd?: number;
      occurredAt?: string;
    }[]
  >([]);
  const [usageTotal, setUsageTotal] = useState<number>();
  const [loadingWallet, setLoadingWallet] = useState(true);
  const [loadingUsage, setLoadingUsage] = useState(false);

  const refreshWallet = useCallback(async () => {
    const { data } = await api.GET("/v1/wallet", {});
    if (data) {
      setBalanceUsd(data.balanceUsd);
      setPricePerMinuteUsd(data.pricePerAudioMinuteUsd);
    }
    setLoadingWallet(false);
  }, []);

  const refreshUsage = useCallback(async () => {
    setLoadingUsage(true);
    const { data } = await api.GET("/v1/usage", {
      params: { query: { limit: 50, offset: 0 } },
    });
    if (data?.items) setUsageItems(data.items);
    if (data?.total != null) setUsageTotal(data.total);
    setLoadingUsage(false);
  }, []);

  useEffect(() => {
    void (async () => {
      const { data } = await api.GET("/v1/meta/voices", {});
      if (data) setMeta(data);
      await refreshWallet();
      await refreshUsage();
    })();
  }, [refreshWallet, refreshUsage]);

  async function handleLogout() {
    await logout();
    onLogout();
  }

  const onTtsDone = () => {
    void refreshWallet();
    void refreshUsage();
  };

  return (
    <div className="app-shell">
      <header className="wallet-strip" style={{ marginBottom: "0.5rem" }}>
        <div>
          <h1 style={{ margin: 0 }}>Orpheus TTS</h1>
          <span className="muted">{getUsername()}</span>
        </div>
        <button type="button" className="secondary" onClick={handleLogout}>
          Log out
        </button>
      </header>

      <WalletCard
        balanceUsd={balanceUsd}
        pricePerMinuteUsd={pricePerMinuteUsd}
        loading={loadingWallet}
      />

      {mainTab === "studio" && (
        <>
          <div className="tabs">
            <button
              type="button"
              className={`tab ${tab === "voice" ? "active" : ""}`}
              onClick={() => setTab("voice")}
            >
              Voice Lab
            </button>
            <button
              type="button"
              className={`tab ${tab === "stream" ? "active" : ""}`}
              onClick={() => setTab("stream")}
            >
              Stream
            </button>
            <button
              type="button"
              className={`tab ${tab === "live" ? "active" : ""}`}
              onClick={() => setTab("live")}
            >
              Live
            </button>
          </div>

          {tab === "voice" && (
            <VoiceLabPanel
              meta={meta}
              voice={voice}
              onVoiceChange={setVoice}
              onDone={onTtsDone}
            />
          )}
          {tab === "stream" && (
            <StreamPanel
              meta={meta}
              voice={voice}
              text={text}
              onTextChange={setText}
              onDone={onTtsDone}
            />
          )}
          {tab === "live" && <LivePanel voice={voice} onDone={onTtsDone} />}
        </>
      )}

      {mainTab === "usage" && (
        <div className="card">
          <h2>Usage history</h2>
          <UsageTable items={usageItems} total={usageTotal} loading={loadingUsage} />
        </div>
      )}

      <nav className="bottom-nav">
        <button
          type="button"
          className={mainTab === "studio" ? "active" : ""}
          onClick={() => setMainTab("studio")}
        >
          Studio
        </button>
        <button
          type="button"
          className={mainTab === "usage" ? "active" : ""}
          onClick={() => {
            setMainTab("usage");
            void refreshUsage();
          }}
        >
          Usage
        </button>
      </nav>
    </div>
  );
}
