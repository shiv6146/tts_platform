package ws

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
	"github.com/tts-platform/api/internal/auth"
	"github.com/tts-platform/api/internal/billing"
	"github.com/tts-platform/api/internal/cache"
	"github.com/tts-platform/api/internal/grpcclient"
	"github.com/tts-platform/api/internal/metrics"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

type inbound struct {
	Type  string `json:"type"`
	Text  string `json:"text"`
	Final bool   `json:"final"`
	Voice string `json:"voice,omitempty"`
}

type control struct {
	Type                   string  `json:"type"`
	Message                string  `json:"message,omitempty"`
	TTFBMs                 int64   `json:"ttfb_ms,omitempty"`
	Error                  string  `json:"error,omitempty"`
	DeliveredAudioSeconds  float64 `json:"delivered_audio_seconds,omitempty"`
}

type LiveHandler struct {
	Inference            *grpcclient.Client
	Wallets              *cache.WalletCache
	Publisher            *billing.Publisher
	Coalesce             time.Duration
	RefreshBal           time.Duration
	MetricsWalletPerUser bool
}

func (h *LiveHandler) observeWallet(ctx context.Context, userID uuid.UUID) {
	if !h.MetricsWalletPerUser {
		return
	}
	snap, err := h.Wallets.Get(ctx, userID)
	if err != nil {
		return
	}
	metrics.ObserveWalletBalance(true, userID, snap.BalanceUSD)
}

func (h *LiveHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	okBal, snap, err := h.Wallets.HasBalance(r.Context(), u.ID)
	if err != nil || !okBal {
		http.Error(w, "insufficient balance", http.StatusPaymentRequired)
		return
	}
	h.observeWallet(r.Context(), u.ID)
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()

	metrics.ActiveStreams.Inc()
	defer metrics.ActiveStreams.Dec()

	requestID := uuid.New().String()
	_ = conn.WriteJSON(control{Type: "ready"})

	live, err := h.Inference.SynthesizeLive(r.Context())
	if err != nil {
		_ = conn.WriteJSON(control{Type: "error", Error: err.Error()})
		return
	}
	defer live.CloseSend()

	coal := billing.NewCoalescer(h.Publisher, u.ID, requestID, "websocket", h.Coalesce)
	budget := snap.BalanceUSD
	price := snap.PricePerAudioMinuteUSD
	lastRefresh := time.Now()
	var firstByte bool
	start := time.Now()
	var audioSeconds float64
	sessionVoice := "tara"

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()

	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			default:
			}
			chunk, err := live.Recv()
			if err != nil {
				return
			}
			if len(chunk.Pcm) == 0 {
				continue
			}
			if !firstByte {
				firstByte = true
				ttfb := time.Since(start).Milliseconds()
				_ = conn.WriteJSON(control{Type: "metadata", TTFBMs: ttfb})
				metrics.TTFB.WithLabelValues("websocket").Observe(time.Since(start).Seconds())
			}
			sec := billing.SecondsFromPCM(len(chunk.Pcm), int(chunk.SampleRate))
			audioSeconds += sec
			cost := billing.CostForSeconds(sec, price)
			budget -= cost
			if budget <= 0 {
				_ = conn.WriteJSON(control{
					Type:                  "insufficient_balance",
					Message:               "delivery budget exhausted",
					DeliveredAudioSeconds: audioSeconds,
				})
				_ = conn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(4020, "insufficient_balance"))
				cancel()
				return
			}
			if err := conn.WriteMessage(websocket.BinaryMessage, chunk.Pcm); err != nil {
				return
			}
			_ = coal.AddPCM(len(chunk.Pcm), int(chunk.SampleRate))
			metrics.AudioSeconds.WithLabelValues("websocket").Add(sec)
		}
	}()

	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			break
		}
		var in inbound
		if err := json.Unmarshal(msg, &in); err != nil || in.Type != "text" {
			continue
		}
		if in.Voice != "" {
			sessionVoice = in.Voice
		}
		if strings.TrimSpace(in.Text) == "" {
			continue
		}
		if err := live.SendText(requestID, in.Text, sessionVoice, in.Final); err != nil {
			_ = conn.WriteJSON(control{Type: "error", Error: err.Error()})
			continue
		}
		if time.Since(lastRefresh) >= h.RefreshBal {
			snap, _ = h.Wallets.Refresh(r.Context(), u.ID)
			budget = snap.BalanceUSD
			lastRefresh = time.Now()
			h.observeWallet(r.Context(), u.ID)
		}
	}

	_ = coal.Flush()
	proc := time.Since(start).Seconds()
	if audioSeconds > 0 {
		metrics.RTF.WithLabelValues("websocket").Observe(proc / audioSeconds)
	}
	metrics.RequestDuration.WithLabelValues("/v1/tts/live", "websocket").Observe(proc)
	h.observeWallet(r.Context(), u.ID)
	_ = conn.WriteJSON(control{Type: "done", DeliveredAudioSeconds: audioSeconds})
}
