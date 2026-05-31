package consumer

import (
	"context"
	"encoding/json"
	"log"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/redis/go-redis/v9"
)

const subject = "billable.tts.v1"
const queueGroup = "metering"

type Event struct {
	IdempotencyKey string    `json:"idempotency_key"`
	UserID         string    `json:"user_id"`
	RequestID      string    `json:"request_id"`
	Transport      string    `json:"transport"`
	AudioSeconds   float64   `json:"audio_seconds"`
	OccurredAt     time.Time `json:"occurred_at"`
}

var (
	eventsProcessed = promauto.NewCounter(prometheus.CounterOpts{
		Name: "metering_events_processed_total",
		Help: "Billable events processed",
	})
	debitErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "metering_debit_errors_total",
		Help: "Wallet debit failures",
	})
)

type Consumer struct {
	pool    *pgxpool.Pool
	redis   *redis.Client
	defPPM  float64
	batch   time.Duration
	pending map[string][]Event
	mu      sync.Mutex
}

func New(pool *pgxpool.Pool, redis *redis.Client, defaultPPM float64, batch time.Duration) *Consumer {
	return &Consumer{
		pool:    pool,
		redis:   redis,
		defPPM:  defaultPPM,
		batch:   batch,
		pending: make(map[string][]Event),
	}
}

func (c *Consumer) Run(ctx context.Context, nc *nats.Conn) error {
	_, err := nc.QueueSubscribe(subject, queueGroup, func(msg *nats.Msg) {
		var e Event
		if err := json.Unmarshal(msg.Data, &e); err != nil {
			log.Printf("bad event: %v", err)
			return
		}
		c.mu.Lock()
		c.pending[e.UserID] = append(c.pending[e.UserID], e)
		c.mu.Unlock()
	})
	if err != nil {
		return err
	}

	ticker := time.NewTicker(c.batch)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			c.flush(ctx)
		}
	}
}

func (c *Consumer) flush(ctx context.Context) {
	c.mu.Lock()
	batch := c.pending
	c.pending = make(map[string][]Event)
	c.mu.Unlock()

	for userID, events := range batch {
		if err := c.debitUser(ctx, userID, events); err != nil {
			log.Printf("debit %s: %v", userID, err)
			debitErrors.Inc()
		} else {
			eventsProcessed.Add(float64(len(events)))
		}
	}
}

func (c *Consumer) debitUser(ctx context.Context, userID string, events []Event) error {
	var totalCost float64
	for _, e := range events {
		ppm, err := c.pricePerMinute(ctx, userID)
		if err != nil {
			return err
		}
		cost := (e.AudioSeconds / 60.0) * ppm
		totalCost += cost
		_, err = c.pool.Exec(ctx, `
			INSERT INTO usage_events (user_id, idempotency_key, request_id, transport, audio_seconds, cost_usd, occurred_at)
			VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
			ON CONFLICT (idempotency_key) DO NOTHING`,
			userID, e.IdempotencyKey, e.RequestID, e.Transport, e.AudioSeconds, cost, e.OccurredAt)
		if err != nil {
			return err
		}
	}
	if totalCost <= 0 {
		return nil
	}
	_, err := c.pool.Exec(ctx, `
		UPDATE wallets SET balance_usd = GREATEST(balance_usd - $2, 0) WHERE user_id = $1::uuid`,
		userID, totalCost)
	if err != nil {
		return err
	}
	_, _ = c.pool.Exec(ctx, `
		INSERT INTO wallet_transactions (user_id, amount_usd, reference_id)
		VALUES ($1::uuid, $2, $3)`,
		userID, -totalCost, events[0].RequestID)
	_ = c.redis.Del(ctx, "user:"+userID+":wallet").Err()
	return nil
}

func (c *Consumer) pricePerMinute(ctx context.Context, userID string) (float64, error) {
	key := "user:" + userID + ":wallet"
	val, err := c.redis.Get(ctx, key).Bytes()
	if err == nil {
		var snap struct {
			PricePerAudioMinuteUSD float64 `json:"price_per_audio_minute_usd"`
		}
		if json.Unmarshal(val, &snap) == nil && snap.PricePerAudioMinuteUSD > 0 {
			return snap.PricePerAudioMinuteUSD, nil
		}
	}
	var ppm *float64
	err = c.pool.QueryRow(ctx, `
		SELECT price_per_audio_minute_usd FROM users WHERE id = $1::uuid`, userID).Scan(&ppm)
	if err != nil {
		return c.defPPM, err
	}
	if ppm != nil {
		return *ppm, nil
	}
	return c.defPPM, nil
}

func RedisAddr(valkeyURL string) string {
	u := strings.TrimPrefix(valkeyURL, "redis://")
	if i := strings.IndexByte(u, '/'); i >= 0 {
		u = u[:i]
	}
	if u == "" {
		return "localhost:6379"
	}
	return u
}
