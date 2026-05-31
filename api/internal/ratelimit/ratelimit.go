package ratelimit

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

type Limiter struct {
	redis *redis.Client
	store *Store
}

func New(redis *redis.Client, store *Store) *Limiter {
	return &Limiter{redis: redis, store: store}
}

func (l *Limiter) Store() *Store {
	return l.store
}

func (l *Limiter) Allow(ctx context.Context, userID uuid.UUID) (bool, time.Duration, error) {
	lim, _, err := l.store.Effective(ctx, userID)
	if err != nil {
		return false, 0, err
	}
	now := time.Now().UTC()
	minute := now.Unix() / 60
	hour := now.Unix() / 3600
	day := now.Unix() / 86400
	uid := userID.String()
	keys := []string{
		fmt.Sprintf("rl:%s:m:%d", uid, minute),
		fmt.Sprintf("rl:%s:h:%d", uid, hour),
		fmt.Sprintf("rl:%s:d:%d", uid, day),
	}
	limits := []int{lim.RPM, lim.RPH, lim.RPD}
	expiries := []time.Duration{120 * time.Second, 7200 * time.Second, 172800 * time.Second}

	pipe := l.redis.Pipeline()
	incrs := make([]*redis.IntCmd, len(keys))
	for i, k := range keys {
		incrs[i] = pipe.Incr(ctx, k)
		pipe.Expire(ctx, k, expiries[i])
	}
	if _, err := pipe.Exec(ctx); err != nil {
		return false, 0, err
	}
	for i, cmd := range incrs {
		n, err := cmd.Result()
		if err != nil {
			return false, 0, err
		}
		if int(n) > limits[i] {
			retry := expiries[i]
			if i == 0 {
				retry = time.Duration(60-now.Second()) * time.Second
			}
			return false, retry, nil
		}
	}
	return true, 0, nil
}

func RetryAfterHeader(d time.Duration) string {
	if d <= 0 {
		d = 60 * time.Second
	}
	return strconv.Itoa(int(d.Seconds()))
}
