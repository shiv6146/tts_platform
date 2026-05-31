package auth

import (
	"context"
	"net/http"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

type ctxKey int

const userCtxKey ctxKey = 1

func WithUser(ctx context.Context, u User) context.Context {
	return context.WithValue(ctx, userCtxKey, u)
}

func UserFromContext(ctx context.Context) (User, bool) {
	u, ok := ctx.Value(userCtxKey).(User)
	return u, ok
}

// Paths served by the OpenAPI mux behind BearerMiddleware but declared public in openapi.yaml.
var publicPaths = map[string]struct{}{
	"/health":            {},
	"/v1/auth/register":  {},
	"/v1/auth/login":     {},
	"/v1/auth/logout":    {},
}

func BearerMiddleware(pool *pgxpool.Pool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if _, ok := publicPaths[r.URL.Path]; ok {
				next.ServeHTTP(w, r)
				return
			}
			token := TokenFromRequest(r)
			if token == "" {
				http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
				return
			}
			u, err := ResolveAPIKey(r.Context(), pool, token)
			if err != nil {
				http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r.WithContext(WithUser(r.Context(), u)))
		})
	}
}

func OptionalUserID(ctx context.Context) uuid.UUID {
	if u, ok := UserFromContext(ctx); ok {
		return u.ID
	}
	return uuid.Nil
}
