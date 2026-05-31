package config

import (
	"os"
	"strconv"
	"time"
)

type Config struct {
	DatabaseURL                    string
	ValkeyURL                      string
	NATSURL                        string
	InferenceGRPCAddr              string
	APIPort                        string
	DefaultUsername                string
	DefaultPassword                string
	DefaultWalletUSD               float64
	PlatformDefaultPricePerMinute  float64
	RateLimitRPM                   int
	RateLimitRPH                   int
	RateLimitRPD                   int
	BillingCoalesce                time.Duration
	DeliveryRefreshStream          time.Duration
	DeliveryRefreshWS              time.Duration
	MetricsWalletPerUser           bool
	MaxConcurrentSynthesis         int
	PlatformAdminKey               string
}

func Load() Config {
	return Config{
		DatabaseURL:                   env("DATABASE_URL", "postgres://tts:tts@localhost:5432/tts?sslmode=disable"),
		ValkeyURL:                     env("VALKEY_URL", "redis://localhost:6379/0"),
		NATSURL:                       env("NATS_URL", "nats://localhost:4222"),
		InferenceGRPCAddr:             env("INFERENCE_GRPC_ADDR", "localhost:50051"),
		APIPort:                       env("API_PORT", "8080"),
		DefaultUsername:               env("DEFAULT_USERNAME", "dev"),
		DefaultPassword:               env("DEFAULT_PASSWORD", "devpassword"),
		DefaultWalletUSD:              envFloat("DEFAULT_WALLET_USD", 20),
		PlatformDefaultPricePerMinute: envFloat("PLATFORM_DEFAULT_PRICE_PER_MINUTE", 0.05),
		RateLimitRPM:                  envInt("RATE_LIMIT_RPM", 60),
		RateLimitRPH:                  envInt("RATE_LIMIT_RPH", 1000),
		RateLimitRPD:                  envInt("RATE_LIMIT_RPD", 10000),
		BillingCoalesce:               time.Duration(envInt("BILLING_COALESCE_MS", 300)) * time.Millisecond,
		DeliveryRefreshStream:         time.Duration(envInt("DELIVERY_BALANCE_REFRESH_STREAM_SEC", 5)) * time.Second,
		DeliveryRefreshWS:             time.Duration(envInt("DELIVERY_BALANCE_REFRESH_WS_SEC", 2)) * time.Second,
		MetricsWalletPerUser:          env("METRICS_WALLET_PER_USER", "false") == "true",
		MaxConcurrentSynthesis:          envInt("MAX_CONCURRENT_SYNTHESIS", 16),
		PlatformAdminKey:              env("PLATFORM_ADMIN_KEY", ""),
	}
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envFloat(k string, def float64) float64 {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.ParseFloat(v, 64); err == nil {
			return n
		}
	}
	return def
}
