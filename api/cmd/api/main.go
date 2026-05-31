package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
	"github.com/nats-io/nats.go"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
	"github.com/tts-platform/api/internal/auth"
	"github.com/tts-platform/api/internal/billing"
	"github.com/tts-platform/api/internal/cache"
	"github.com/tts-platform/api/internal/config"
	"github.com/tts-platform/api/internal/db"
	"github.com/tts-platform/api/internal/docs"
	"github.com/tts-platform/api/internal/gen"
	"github.com/tts-platform/api/internal/grpcclient"
	"github.com/tts-platform/api/internal/handler"
	"github.com/tts-platform/api/internal/metrics"
	"github.com/tts-platform/api/internal/ratelimit"
	"github.com/tts-platform/api/internal/ws"
)

func main() {
	cfg := config.Load()
	ctx := context.Background()

	if err := db.Migrate(cfg.DatabaseURL); err != nil {
		log.Fatalf("migrate: %v", err)
	}
	pool, err := db.Connect(ctx, cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("db: %v", err)
	}
	defer pool.Close()

	rdb := redis.NewClient(&redis.Options{Addr: redisAddr(cfg.ValkeyURL)})
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Printf("valkey: %v", err)
	}

	nc, err := nats.Connect(cfg.NATSURL)
	if err != nil {
		log.Fatalf("nats: %v", err)
	}
	defer nc.Close()

	wallets := cache.NewWalletCache(pool, rdb, cfg.PlatformDefaultPricePerMinute)
	if cfg.MetricsWalletPerUser {
		metrics.RegisterWalletBalance(prometheus.DefaultRegisterer)
	}

	inf, err := grpcclient.Dial(ctx, cfg.InferenceGRPCAddr)
	if err != nil {
		log.Fatalf("inference grpc: %v", err)
	}
	defer inf.Close()

	_, devKey, err := auth.EnsureDefaultUser(ctx, pool, cfg.DefaultUsername, cfg.DefaultPassword,
		cfg.DefaultWalletUSD, cfg.PlatformDefaultPricePerMinute)
	if err != nil {
		log.Fatalf("seed user: %v", err)
	}
	if devKey != "" {
		log.Printf("default API key (save now): %s", devKey)
	}

	live := &ws.LiveHandler{
		Inference:            inf,
		Wallets:              wallets,
		Publisher:            billing.NewPublisher(nc),
		Coalesce:             cfg.BillingCoalesce,
		RefreshBal:           cfg.DeliveryRefreshWS,
		MetricsWalletPerUser: cfg.MetricsWalletPerUser,
	}

	srv := &handler.Server{
		Pool:      pool,
		Wallets:   wallets,
		Inference: inf,
		Publisher: billing.NewPublisher(nc),
		Limiter:   ratelimit.New(rdb, cfg.RateLimitRPM, cfg.RateLimitRPH, cfg.RateLimitRPD),
		Cfg:       cfg,
		Live:      live,
	}

	router := chi.NewRouter()
	router.Use(chimw.RequestID, chimw.RealIP, chimw.Logger, chimw.Recoverer)
	router.Use(handler.HTTPMetricsMiddleware)
	router.Handle("/metrics", promhttp.Handler())
	router.Mount("/docs", http.StripPrefix("/docs", docs.Handler()))
	router.Get("/health", srv.GetHealth)
	router.Get("/livez", srv.GetHealth)
	router.Post("/v1/auth/register", srv.RegisterUser)

	router.Group(func(pr chi.Router) {
		pr.Use(auth.BearerMiddleware(pool))
		pr.Use(rateLimitMiddleware(srv.Limiter))
		pr.Mount("/", gen.HandlerFromMux(srv, pr))
	})

	addr := ":" + cfg.APIPort
	httpSrv := &http.Server{Addr: addr, Handler: router}
	go func() {
		log.Printf("api listening on %s", addr)
		if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
}

func rateLimitMiddleware(l *ratelimit.Limiter) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if l == nil {
				next.ServeHTTP(w, r)
				return
			}
			u, ok := auth.UserFromContext(r.Context())
			if !ok {
				next.ServeHTTP(w, r)
				return
			}
			allowed, retry, err := l.Allow(r.Context(), u.ID)
			if err != nil {
				http.Error(w, "rate limit error", http.StatusInternalServerError)
				return
			}
			if !allowed {
				w.Header().Set("Retry-After", ratelimit.RetryAfterHeader(retry))
				http.Error(w, "rate limited", http.StatusTooManyRequests)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func redisAddr(valkeyURL string) string {
	u := strings.TrimPrefix(valkeyURL, "redis://")
	if i := strings.IndexByte(u, '/'); i >= 0 {
		u = u[:i]
	}
	if u == "" {
		return "localhost:6379"
	}
	return u
}
