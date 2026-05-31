package ratelimit

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// Limits holds per-window request caps (requests per minute/hour/day).
type Limits struct {
	RPM int `json:"rpm"`
	RPH int `json:"rph"`
	RPD int `json:"rpd"`
}

func (l Limits) valid() bool {
	return l.RPM > 0 && l.RPH > 0 && l.RPD > 0
}

type Store struct {
	redis    *redis.Client
	pool     *pgxpool.Pool
	defaults Limits
}

func NewStore(redis *redis.Client, pool *pgxpool.Pool, defaults Limits) *Store {
	return &Store{redis: redis, pool: pool, defaults: defaults}
}

func cacheKey(userID uuid.UUID) string {
	return fmt.Sprintf("rlcfg:%s", userID.String())
}

func (s *Store) Defaults() Limits {
	return s.defaults
}

func (s *Store) Effective(ctx context.Context, userID uuid.UUID) (Limits, bool, error) {
	if s.redis != nil {
		raw, err := s.redis.Get(ctx, cacheKey(userID)).Bytes()
		if err == nil {
			var lim Limits
			if json.Unmarshal(raw, &lim) == nil && lim.valid() {
				return lim, true, nil
			}
		} else if !errors.Is(err, redis.Nil) {
			return Limits{}, false, err
		}
	}
	if s.pool != nil {
		var rpm, rph, rpd int
		err := s.pool.QueryRow(ctx, `
			SELECT rpm, rph, rpd FROM user_rate_limits WHERE user_id = $1`, userID).
			Scan(&rpm, &rph, &rpd)
		if err == nil {
			lim := Limits{RPM: rpm, RPH: rph, RPD: rpd}
			_ = s.cache(ctx, userID, lim)
			return lim, true, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return Limits{}, false, err
		}
	}
	return s.defaults, false, nil
}

func (s *Store) SetOverride(ctx context.Context, userID uuid.UUID, lim Limits) error {
	if !lim.valid() {
		return fmt.Errorf("rpm, rph, and rpd must be positive")
	}
	if s.pool == nil {
		return errors.New("database not configured")
	}
	_, err := s.pool.Exec(ctx, `
		INSERT INTO user_rate_limits (user_id, rpm, rph, rpd, updated_at)
		VALUES ($1, $2, $3, $4, now())
		ON CONFLICT (user_id) DO UPDATE SET
			rpm = EXCLUDED.rpm,
			rph = EXCLUDED.rph,
			rpd = EXCLUDED.rpd,
			updated_at = now()`, userID, lim.RPM, lim.RPH, lim.RPD)
	if err != nil {
		return err
	}
	return s.cache(ctx, userID, lim)
}

func (s *Store) ClearOverride(ctx context.Context, userID uuid.UUID) error {
	if s.pool != nil {
		_, err := s.pool.Exec(ctx, `DELETE FROM user_rate_limits WHERE user_id = $1`, userID)
		if err != nil {
			return err
		}
	}
	if s.redis != nil {
		_ = s.redis.Del(ctx, cacheKey(userID)).Err()
	}
	return nil
}

func (s *Store) cache(ctx context.Context, userID uuid.UUID, lim Limits) error {
	if s.redis == nil {
		return nil
	}
	b, err := json.Marshal(lim)
	if err != nil {
		return err
	}
	return s.redis.Set(ctx, cacheKey(userID), b, 5*time.Minute).Err()
}
