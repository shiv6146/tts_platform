package ws

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
	"github.com/tts-platform/api/internal/auth"
	"github.com/tts-platform/api/internal/billing"
	"github.com/tts-platform/api/internal/cache"
	"github.com/tts-platform/api/internal/grpcclient"
	"github.com/tts-platform/api/internal/metrics"
	"github.com/tts-platform/api/internal/synthlimit"
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
	Type                  string  `json:"type"`
	Message               string  `json:"message,omitempty"`
	TTFBMs                int64   `json:"ttfb_ms,omitempty"`
	Error                 string  `json:"error,omitempty"`
	DeliveredAudioSeconds float64 `json:"delivered_audio_seconds,omitempty"`
}

type LiveHandler struct {
	Inference            *grpcclient.Client
	Wallets              *cache.WalletCache
	Publisher            *billing.Publisher
	Synth                *synthlimit.Limiter
	Coalesce             time.Duration
	RefreshBal           time.Duration
	MetricsWalletPerUser bool
}

// utteranceMetrics tracks one live phrase (SendText → last PCM), aligned with http_stream timing.
type utteranceMetrics struct {
	start       time.Time
	lastPCM     time.Time
	audioSec    float64
	firstByte   bool
	active      bool
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

func flushUtterance(utt *utteranceMetrics) {
	if !utt.active || utt.audioSec <= 0 {
		utt.active = false
		utt.firstByte = false
		utt.audioSec = 0
		return
	}
	end := utt.lastPCM
	if end.IsZero() {
		end = time.Now()
	}
	proc := end.Sub(utt.start).Seconds()
	if proc > 0 {
		metrics.RTF.WithLabelValues("websocket").Observe(proc / utt.audioSec)
		metrics.RequestDuration.WithLabelValues("/v1/tts/live", "websocket").Observe(proc)
	}
	utt.active = false
	utt.firstByte = false
	utt.audioSec = 0
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
	if h.Synth != nil {
		if err := h.Synth.Acquire(r.Context()); err != nil {
			http.Error(w, "synthesis capacity exceeded", http.StatusServiceUnavailable)
			return
		}
		defer h.Synth.Release()
	}
	done := metrics.TrackActiveStream()
	defer done()
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()

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
	sessionVoice := "tara"
	var totalAudioSeconds float64

	var mu sync.Mutex
	var utt utteranceMetrics

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
			// Inference emits seq=-1 after each final utterance's PCM stream ends.
			if chunk.Seq < 0 {
				mu.Lock()
				audio := utt.audioSec
				flushUtterance(&utt)
				mu.Unlock()
				_ = conn.WriteJSON(control{
					Type:                  "utterance_done",
					DeliveredAudioSeconds: audio,
				})
				continue
			}
			if len(chunk.Pcm) == 0 {
				continue
			}
			sec := billing.SecondsFromPCM(len(chunk.Pcm), int(chunk.SampleRate))

			mu.Lock()
			if utt.active && !utt.firstByte {
				utt.firstByte = true
				ttfb := time.Since(utt.start)
				_ = conn.WriteJSON(control{Type: "metadata", TTFBMs: ttfb.Milliseconds()})
				metrics.TTFB.WithLabelValues("websocket").Observe(ttfb.Seconds())
			}
			if utt.active {
				utt.audioSec += sec
				utt.lastPCM = time.Now()
			}
			totalAudioSeconds += sec
			mu.Unlock()

			cost := billing.CostForSeconds(sec, price)
			budget -= cost
			if budget <= 0 {
				_ = conn.WriteJSON(control{
					Type:                  "insufficient_balance",
					Message:               "delivery budget exhausted",
					DeliveredAudioSeconds: totalAudioSeconds,
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

		if in.Final {
			mu.Lock()
			flushUtterance(&utt)
			utt = utteranceMetrics{start: time.Now(), active: true}
			mu.Unlock()
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

	mu.Lock()
	flushUtterance(&utt)
	mu.Unlock()

	_ = coal.Flush()
	h.observeWallet(r.Context(), u.ID)
	_ = conn.WriteJSON(control{Type: "done", DeliveredAudioSeconds: totalAudioSeconds})
}
