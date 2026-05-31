package metrics

import (
	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	TTFB = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "tts_time_to_first_byte_seconds",
		Help:    "Time to first audio byte delivered to client",
		Buckets: append([]float64{0.01, 0.025}, prometheus.ExponentialBuckets(0.05, 2, 10)...),
	}, []string{"transport"})

	RequestDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "tts_request_duration_seconds",
		Help:    "End-to-end TTS request duration",
		Buckets: prometheus.ExponentialBuckets(0.1, 2, 12),
	}, []string{"route", "transport"})

	RTF = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "tts_realtime_factor",
		Help:    "Processing time divided by audio duration",
		Buckets: []float64{0.1, 0.25, 0.5, 1, 2, 5, 10},
	}, []string{"transport"})

	ActiveStreams = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "tts_active_streams",
		Help: "Active HTTP stream and WebSocket sessions",
	})

	AudioSeconds = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "tts_audio_seconds_generated_total",
		Help: "Audio seconds delivered to clients",
	}, []string{"transport"})

	HTTPDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "HTTP request latency",
		Buckets: prometheus.DefBuckets,
	}, []string{"method", "route", "status"})

	CacheL2Miss = promauto.NewCounter(prometheus.CounterOpts{
		Name: "cache_l2_miss_total",
		Help: "Valkey/Postgres wallet cache loads",
	})

	WalletBalance = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "wallet_balance_usd",
		Help: "Per-user wallet balance (METRICS_WALLET_PER_USER)",
	}, []string{"user_id"})
)

func ObserveWalletBalance(enabled bool, userID uuid.UUID, balance float64) {
	if !enabled {
		return
	}
	WalletBalance.WithLabelValues(userID.String()).Set(balance)
}

func RegisterWalletBalance(reg prometheus.Registerer) {
	reg.MustRegister(WalletBalance)
}
