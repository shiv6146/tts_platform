package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/tts-platform/api/internal/auth"
	"github.com/tts-platform/api/internal/ratelimit"
)

type rateLimitResponse struct {
	UserID   string `json:"user_id,omitempty"`
	RPM      int    `json:"rpm"`
	RPH      int    `json:"rph"`
	RPD      int    `json:"rpd"`
	Source   string `json:"source"`
	Defaults bool   `json:"defaults,omitempty"`
}

type rateLimitRequest struct {
	RPM int `json:"rpm"`
	RPH int `json:"rph"`
	RPD int `json:"rpd"`
}

func (s *Server) GetAccountRateLimit(w http.ResponseWriter, r *http.Request) {
	u, ok := auth.UserFromContext(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	writeRateLimit(r.Context(), w, u.ID, s.Limiter.Store())
}

func (s *Server) AdminGetUserRateLimit(w http.ResponseWriter, r *http.Request) {
	userID, err := uuid.Parse(chi.URLParam(r, "userId"))
	if err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	writeRateLimit(r.Context(), w, userID, s.Limiter.Store())
}

func writeRateLimit(ctx context.Context, w http.ResponseWriter, userID uuid.UUID, store *ratelimit.Store) {
	lim, override, err := store.Effective(ctx, userID)
	if err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	src := "default"
	if override {
		src = "override"
	}
	writeJSON(w, http.StatusOK, rateLimitResponse{
		UserID: userID.String(),
		RPM:    lim.RPM,
		RPH:    lim.RPH,
		RPD:    lim.RPD,
		Source: src,
	})
}

func (s *Server) AdminSetUserRateLimit(w http.ResponseWriter, r *http.Request) {
	userID, err := uuid.Parse(chi.URLParam(r, "userId"))
	if err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	var req rateLimitRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	lim := ratelimit.Limits{RPM: req.RPM, RPH: req.RPH, RPD: req.RPD}
	if err := s.Limiter.Store().SetOverride(r.Context(), userID, lim); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeRateLimit(r.Context(), w, userID, s.Limiter.Store())
}

func (s *Server) AdminDeleteUserRateLimit(w http.ResponseWriter, r *http.Request) {
	userID, err := uuid.Parse(chi.URLParam(r, "userId"))
	if err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	if err := s.Limiter.Store().ClearOverride(r.Context(), userID); err != nil {
		http.Error(w, "error", http.StatusInternalServerError)
		return
	}
	def := s.Limiter.Store().Defaults()
	writeJSON(w, http.StatusOK, rateLimitResponse{
		UserID:   userID.String(),
		RPM:      def.RPM,
		RPH:      def.RPH,
		RPD:      def.RPD,
		Source:   "default",
		Defaults: true,
	})
}

// AdminMiddleware protects ops routes with PLATFORM_ADMIN_KEY (Bearer or X-Admin-Key).
func AdminMiddleware(adminKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if adminKey == "" {
				http.Error(w, "admin API not configured", http.StatusNotImplemented)
				return
			}
			key := r.Header.Get("X-Admin-Key")
			if key == "" {
				if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
					key = strings.TrimPrefix(h, "Bearer ")
				}
			}
			if key != adminKey {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
