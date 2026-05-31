package handler

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	openapi_types "github.com/oapi-codegen/runtime/types"
	"github.com/tts-platform/api/internal/audio"
	"github.com/tts-platform/api/internal/auth"
	"github.com/tts-platform/api/internal/billing"
	"github.com/tts-platform/api/internal/cache"
	"github.com/tts-platform/api/internal/config"
	"github.com/tts-platform/api/internal/gen"
	"github.com/tts-platform/api/internal/grpcclient"
	"github.com/tts-platform/api/internal/metrics"
	"github.com/tts-platform/api/internal/ratelimit"
	"github.com/tts-platform/api/internal/synthlimit"
)

type Server struct {
	Pool      *pgxpool.Pool
	Wallets   *cache.WalletCache
	Inference *grpcclient.Client
	Publisher *billing.Publisher
	Limiter   *ratelimit.Limiter
	Synth     *synthlimit.Limiter
	Cfg       config.Config
	Live      http.Handler // WebSocket live TTS (set from main)
}

// LiveTTSWebSocket implements gen.ServerInterface for OpenAPI /v1/tts/live.
func (s *Server) LiveTTSWebSocket(w http.ResponseWriter, r *http.Request) {
	if s.Live != nil {
		s.Live.ServeHTTP(w, r)
		return
	}
	http.Error(w, "live TTS not configured", http.StatusInternalServerError)
}

func (s *Server) GetHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, gen.HealthResponse{Status: "ok"})
}

func (s *Server) writeAuthSession(w http.ResponseWriter, code int, u auth.User, apiKey string) {
	auth.SetAuthCookie(w, apiKey)
	id := openapi_types.UUID(u.ID)
	writeJSON(w, code, gen.AuthSessionResponse{
		Id: id, Username: u.Username, ApiKey: apiKey,
	})
}

func (s *Server) LogoutUser(w http.ResponseWriter, r *http.Request) {
	auth.ClearAuthCookie(w)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) RegisterUser(w http.ResponseWriter, r *http.Request) {
	var req gen.RegisterRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	u, err := auth.Register(r.Context(), s.Pool, req.Username, req.Password, s.Cfg.DefaultWalletUSD)
	if err != nil {
		http.Error(w, "conflict", http.StatusConflict)
		return
	}
	_, _, secret, err := auth.CreateAPIKey(r.Context(), s.Pool, u.ID)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	s.writeAuthSession(w, http.StatusCreated, u, secret)
}

func (s *Server) LoginUser(w http.ResponseWriter, r *http.Request) {
	var req gen.LoginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	u, err := auth.AuthenticatePassword(r.Context(), s.Pool, req.Username, req.Password)
	if err != nil {
		http.Error(w, `{"error":"invalid credentials"}`, http.StatusUnauthorized)
		return
	}
	_, _, secret, err := auth.CreateAPIKey(r.Context(), s.Pool, u.ID)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	s.writeAuthSession(w, http.StatusOK, u, secret)
}

func (s *Server) CreateApiKey(w http.ResponseWriter, r *http.Request) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	id, prefix, secret, err := auth.CreateAPIKey(r.Context(), s.Pool, u.ID)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	uid := openapi_types.UUID(id)
	writeJSON(w, http.StatusCreated, gen.CreateApiKeyResponse{
		Id: &uid, Prefix: &prefix, Secret: &secret,
	})
}

func (s *Server) ListApiKeys(w http.ResponseWriter, r *http.Request) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	keys, err := auth.ListAPIKeys(r.Context(), s.Pool, u.ID)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	summaries := make([]gen.ApiKeySummary, 0, len(keys))
	for _, k := range keys {
		id := openapi_types.UUID(k.ID)
		p := k.Prefix
		summaries = append(summaries, gen.ApiKeySummary{Id: &id, Prefix: &p})
	}
	writeJSON(w, http.StatusOK, gen.ApiKeyListResponse{Keys: &summaries})
}

func (s *Server) RevokeApiKey(w http.ResponseWriter, r *http.Request, keyId gen.KeyId) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	if err := auth.RevokeAPIKey(r.Context(), s.Pool, u.ID, uuid.UUID(keyId)); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) GetWallet(w http.ResponseWriter, r *http.Request) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	snap, err := s.Wallets.Get(r.Context(), u.ID)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	metrics.ObserveWalletBalance(s.Cfg.MetricsWalletPerUser, u.ID, snap.BalanceUSD)
	bal := float32(snap.BalanceUSD)
	ppm := float32(snap.PricePerAudioMinuteUSD)
	writeJSON(w, http.StatusOK, gen.WalletResponse{
		BalanceUsd: &bal, PricePerAudioMinuteUsd: &ppm,
	})
}

func (s *Server) ListUsage(w http.ResponseWriter, r *http.Request, params gen.ListUsageParams) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	limit := 50
	if params.Limit != nil {
		limit = *params.Limit
	}
	offset := 0
	if params.Offset != nil {
		offset = *params.Offset
	}
	var total int
	if err := s.Pool.QueryRow(r.Context(), `
		SELECT COUNT(*) FROM usage_events WHERE user_id = $1`, u.ID).Scan(&total); err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	rows, err := s.Pool.Query(r.Context(), `
		SELECT id, request_id, transport, audio_seconds, cost_usd, occurred_at
		FROM usage_events WHERE user_id = $1
		ORDER BY occurred_at DESC LIMIT $2 OFFSET $3`, u.ID, limit, offset)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	var items []gen.UsageEvent
	for rows.Next() {
		var id uuid.UUID
		var reqID, transport string
		var audioSec, cost float64
		var at time.Time
		if err := rows.Scan(&id, &reqID, &transport, &audioSec, &cost, &at); err != nil {
			http.Error(w, "error", http.StatusInternalServerError)
			return
		}
		uid := openapi_types.UUID(id)
		occ := at
		a := float32(audioSec)
		c := float32(cost)
		items = append(items, gen.UsageEvent{
			Id: &uid, RequestId: &reqID, Transport: &transport,
			AudioSeconds: &a, CostUsd: &c, OccurredAt: &occ,
		})
	}
	writeJSON(w, http.StatusOK, gen.UsageListResponse{Items: &items, Total: &total})
}

func (s *Server) observeWallet(ctx context.Context, userID uuid.UUID) {
	if !s.Cfg.MetricsWalletPerUser {
		return
	}
	snap, err := s.Wallets.Get(ctx, userID)
	if err != nil {
		return
	}
	metrics.ObserveWalletBalance(true, userID, snap.BalanceUSD)
}

func (s *Server) admit(w http.ResponseWriter, r *http.Request, userID uuid.UUID) (cache.WalletSnapshot, bool) {
	ok, snap, err := s.Wallets.HasBalance(r.Context(), userID)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return cache.WalletSnapshot{}, false
	}
	if !ok {
		http.Error(w, "insufficient balance", http.StatusPaymentRequired)
		return cache.WalletSnapshot{}, false
	}
	s.observeWallet(r.Context(), userID)
	return snap, true
}

func (s *Server) acquireSynthesis(w http.ResponseWriter, r *http.Request) bool {
	if s.Synth == nil {
		return true
	}
	if err := s.Synth.Acquire(r.Context()); err != nil {
		if errors.Is(err, context.Canceled) {
			return false
		}
		http.Error(w, "synthesis capacity exceeded", http.StatusServiceUnavailable)
		return false
	}
	return true
}

func (s *Server) CreateAsyncTTS(w http.ResponseWriter, r *http.Request) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	snap, ok := s.admit(w, r, u.ID)
	if !ok {
		return
	}
	var req gen.TTSRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	voice := "tara"
	if req.Voice != nil {
		voice = *req.Voice
	}
	var jobID uuid.UUID
	err := s.Pool.QueryRow(r.Context(), `
		INSERT INTO tts_jobs (user_id, input, voice, status)
		VALUES ($1, $2, $3, 'pending') RETURNING id`,
		u.ID, req.Text, voice).Scan(&jobID)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	jid := openapi_types.UUID(jobID)
	writeJSON(w, http.StatusAccepted, gen.AsyncJobResponse{JobId: &jid})

	go s.runAsyncJob(jobID, u.ID, req.Text, voice, snap)
}

func (s *Server) runAsyncJob(jobID, userID uuid.UUID, text, voice string, snap cache.WalletSnapshot) {
	ctx := context.Background()
	if s.Synth != nil {
		if err := s.Synth.Acquire(ctx); err != nil {
			msg := "synthesis capacity exceeded"
			_, _ = s.Pool.Exec(ctx, `UPDATE tts_jobs SET status = 'failed', error = $2, updated_at = now() WHERE id = $1`, jobID, msg)
			return
		}
		defer s.Synth.Release()
	}
	_, _ = s.Pool.Exec(ctx, `UPDATE tts_jobs SET status = 'running', updated_at = now() WHERE id = $1`, jobID)
	requestID := jobID.String()
	stream, err := s.Inference.Synthesize(ctx, requestID, text, voice)
	if err != nil {
		msg := err.Error()
		_, _ = s.Pool.Exec(ctx, `UPDATE tts_jobs SET status = 'failed', error = $2, updated_at = now() WHERE id = $1`, jobID, msg)
		return
	}
	metrics.ActiveStreams.Inc()
	defer metrics.ActiveStreams.Dec()

	var pcm []byte
	coal := billing.NewCoalescer(s.Publisher, userID, requestID, "http_async", s.Cfg.BillingCoalesce)
	budget := snap.BalanceUSD
	start := time.Now()
	var audioSeconds float64
	var first bool
	err = grpcclient.CopyAudioStream(ctx, stream, func(chunk []byte, sampleRate int32, _ int64) error {
		if !first {
			first = true
			metrics.TTFB.WithLabelValues("http_async").Observe(time.Since(start).Seconds())
		}
		sec := billing.SecondsFromPCM(len(chunk), int(sampleRate))
		budget -= billing.CostForSeconds(sec, snap.PricePerAudioMinuteUSD)
		if budget < 0 {
			return fmt.Errorf("insufficient balance")
		}
		audioSeconds += sec
		pcm = append(pcm, chunk...)
		_ = coal.AddPCM(len(chunk), int(sampleRate))
		metrics.AudioSeconds.WithLabelValues("http_async").Add(sec)
		return nil
	})
	_ = coal.Flush()
	proc := time.Since(start).Seconds()
	if audioSeconds > 0 {
		metrics.RTF.WithLabelValues("http_async").Observe(proc / audioSeconds)
	}
	metrics.RequestDuration.WithLabelValues("/v1/tts/async", "http_async").Observe(proc)
	s.observeWallet(ctx, userID)
	if err != nil {
		msg := err.Error()
		_, _ = s.Pool.Exec(ctx, `UPDATE tts_jobs SET status = 'failed', error = $2, updated_at = now() WHERE id = $1`, jobID, msg)
		return
	}
	wav := audio.WrapWAV(pcm, audio.SampleRate)
	_, _ = s.Pool.Exec(ctx, `
		UPDATE tts_jobs SET status = 'completed', audio_data = $2, updated_at = now(), completed_at = now()
		WHERE id = $1`, jobID, wav)
}

func (s *Server) GetAsyncJob(w http.ResponseWriter, r *http.Request, jobId gen.JobId) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	var status string
	var errMsg *string
	err := s.Pool.QueryRow(r.Context(), `
		SELECT status::text, error FROM tts_jobs WHERE id = $1 AND user_id = $2`,
		uuid.UUID(jobId), u.ID).Scan(&status, &errMsg)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	jid := openapi_types.UUID(uuid.UUID(jobId))
	st := gen.AsyncJobStatusResponseStatus(status)
	writeJSON(w, http.StatusOK, gen.AsyncJobStatusResponse{JobId: &jid, Status: &st, Error: errMsg})
}

func (s *Server) GetAsyncJobAudio(w http.ResponseWriter, r *http.Request, jobId gen.JobId) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	var data []byte
	var status string
	err := s.Pool.QueryRow(r.Context(), `
		SELECT status::text, audio_data FROM tts_jobs WHERE id = $1 AND user_id = $2`,
		uuid.UUID(jobId), u.ID).Scan(&status, &data)
	if err != nil || status != "completed" || len(data) == 0 {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "audio/wav")
	_, _ = w.Write(data)
}

func (s *Server) StreamTTS(w http.ResponseWriter, r *http.Request) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	snap, ok := s.admit(w, r, u.ID)
	if !ok {
		return
	}
	var req gen.TTSRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	voice := "tara"
	if req.Voice != nil {
		voice = *req.Voice
	}
	if !s.acquireSynthesis(w, r) {
		return
	}
	defer s.Synth.Release()
	requestID := uuid.New().String()
	stream, err := s.Inference.Synthesize(r.Context(), requestID, req.Text, voice)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}

	metrics.ActiveStreams.Inc()
	defer metrics.ActiveStreams.Dec()

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Transfer-Encoding", "chunked")
	w.WriteHeader(http.StatusOK)
	flusher, _ := w.(http.Flusher)

	coal := billing.NewCoalescer(s.Publisher, u.ID, requestID, "http_stream", s.Cfg.BillingCoalesce)
	budget := snap.BalanceUSD
	start := time.Now()
	var audioSeconds float64
	var first bool
	lastRefresh := time.Now()

	err = grpcclient.CopyAudioStream(r.Context(), stream, func(chunk []byte, sampleRate int32, _ int64) error {
		if !first {
			first = true
			metrics.TTFB.WithLabelValues("http_stream").Observe(time.Since(start).Seconds())
		}
		sec := billing.SecondsFromPCM(len(chunk), int(sampleRate))
		budget -= billing.CostForSeconds(sec, snap.PricePerAudioMinuteUSD)
		if budget <= 0 {
			return fmt.Errorf("insufficient balance")
		}
		audioSeconds += sec
		if _, err := w.Write(chunk); err != nil {
			return err
		}
		if flusher != nil {
			flusher.Flush()
		}
		_ = coal.AddPCM(len(chunk), int(sampleRate))
		metrics.AudioSeconds.WithLabelValues("http_stream").Add(sec)
		if time.Since(lastRefresh) >= s.Cfg.DeliveryRefreshStream {
			snap, _ = s.Wallets.Refresh(r.Context(), u.ID)
			budget = snap.BalanceUSD
			lastRefresh = time.Now()
			s.observeWallet(r.Context(), u.ID)
		}
		return nil
	})
	_ = coal.Flush()
	proc := time.Since(start).Seconds()
	if audioSeconds > 0 {
		metrics.RTF.WithLabelValues("http_stream").Observe(proc / audioSeconds)
	}
	metrics.RequestDuration.WithLabelValues("/v1/tts/stream", "http_stream").Observe(proc)
	s.observeWallet(r.Context(), u.ID)
	if err != nil && !errors.Is(err, io.EOF) {
		return
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func HTTPMetricsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
		next.ServeHTTP(ww, r)
		route := r.URL.Path
		metrics.HTTPDuration.WithLabelValues(r.Method, route, fmt.Sprintf("%d", ww.Status())).Observe(time.Since(start).Seconds())
	})
}
