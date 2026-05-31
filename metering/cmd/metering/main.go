package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
	"github.com/tts-platform/metering/internal/consumer"
)

func main() {
	dsn := env("DATABASE_URL", "postgres://tts:tts@localhost:5432/tts?sslmode=disable")
	valkey := env("VALKEY_URL", "redis://localhost:6379/0")
	natsURL := env("NATS_URL", "nats://localhost:4222")
	defPPM := envFloat("PLATFORM_DEFAULT_PRICE_PER_MINUTE", 0.05)
	port := env("METERING_METRICS_PORT", "8081")

	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		log.Fatalf("db: %v", err)
	}
	defer pool.Close()

	rdb := redis.NewClient(&redis.Options{Addr: consumer.RedisAddr(valkey)})
	nc, err := nats.Connect(natsURL)
	if err != nil {
		log.Fatalf("nats: %v", err)
	}
	defer nc.Close()

	go func() {
		http.Handle("/metrics", promhttp.Handler())
		http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		})
		log.Printf("metering metrics on :%s", port)
		log.Fatal(http.ListenAndServe(":"+port, nil))
	}()

	c := consumer.New(pool, rdb, defPPM, 300*time.Millisecond)
	log.Printf("metering consumer started")
	if err := c.Run(ctx, nc); err != nil {
		log.Fatalf("consumer: %v", err)
	}
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envFloat(k string, def float64) float64 {
	v := os.Getenv(k)
	if v == "" {
		return def
	}
	f, err := strconv.ParseFloat(v, 64)
	if err != nil {
		return def
	}
	return f
}
