package cache

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/mailgun/groupcache/v2"
	"github.com/redis/go-redis/v9"
	"github.com/tts-platform/api/internal/metrics"
)

const walletGroupName = "wallet"
const walletTTL = 45 * time.Second

type WalletSnapshot struct {
	BalanceUSD             float64 `json:"balance_usd"`
	PricePerAudioMinuteUSD float64 `json:"price_per_audio_minute_usd"`
}

type WalletCache struct {
	pool   *pgxpool.Pool
	redis  *redis.Client
	group  *groupcache.Group
	defPPM float64
}

func NewWalletCache(pool *pgxpool.Pool, redis *redis.Client, defaultPricePerMinute float64) *WalletCache {
	c := &WalletCache{pool: pool, redis: redis, defPPM: defaultPricePerMinute}
	c.group = groupcache.NewGroup(walletGroupName, 64<<20, groupcache.GetterFunc(c.getter))
	return c
}

func walletKey(userID uuid.UUID) string {
	return fmt.Sprintf("user:%s:wallet", userID.String())
}

func (c *WalletCache) getter(ctx context.Context, key string, dest groupcache.Sink) error {
	userID, err := uuid.Parse(key)
	if err != nil {
		return err
	}
	snap, err := c.loadL2OrDB(ctx, userID)
	if err != nil {
		return err
	}
	b, err := json.Marshal(snap)
	if err != nil {
		return err
	}
	return dest.SetBytes(b, time.Now().Add(walletTTL))
}

func (c *WalletCache) loadL2OrDB(ctx context.Context, userID uuid.UUID) (WalletSnapshot, error) {
	k := walletKey(userID)
	val, err := c.redis.Get(ctx, k).Bytes()
	if err == nil {
		var snap WalletSnapshot
		if json.Unmarshal(val, &snap) == nil {
			return snap, nil
		}
	}
	metrics.CacheL2Miss.Inc()
	snap, err := c.loadDB(ctx, userID)
	if err != nil {
		return WalletSnapshot{}, err
	}
	b, _ := json.Marshal(snap)
	_ = c.redis.Set(ctx, k, b, walletTTL).Err()
	return snap, nil
}

func (c *WalletCache) loadDB(ctx context.Context, userID uuid.UUID) (WalletSnapshot, error) {
	var balance float64
	var price *float64
	err := c.pool.QueryRow(ctx, `
		SELECT w.balance_usd, u.price_per_audio_minute_usd
		FROM wallets w
		JOIN users u ON u.id = w.user_id
		WHERE w.user_id = $1`, userID).Scan(&balance, &price)
	if err != nil {
		return WalletSnapshot{}, err
	}
	ppm := c.defPPM
	if price != nil {
		ppm = *price
	}
	return WalletSnapshot{BalanceUSD: balance, PricePerAudioMinuteUSD: ppm}, nil
}

func (c *WalletCache) Get(ctx context.Context, userID uuid.UUID) (WalletSnapshot, error) {
	var data []byte
	if err := c.group.Get(ctx, userID.String(), groupcache.AllocatingByteSliceSink(&data)); err != nil {
		return WalletSnapshot{}, err
	}
	var snap WalletSnapshot
	if err := json.Unmarshal(data, &snap); err != nil {
		return WalletSnapshot{}, err
	}
	return snap, nil
}

func (c *WalletCache) Invalidate(ctx context.Context, userID uuid.UUID) {
	_ = c.group.Remove(ctx, userID.String())
	_ = c.redis.Del(ctx, walletKey(userID)).Err()
}

func (c *WalletCache) HasBalance(ctx context.Context, userID uuid.UUID) (bool, WalletSnapshot, error) {
	snap, err := c.Get(ctx, userID)
	if err != nil {
		return false, snap, err
	}
	return snap.BalanceUSD > 0, snap, nil
}

// Refresh fetches latest balance from Valkey/Postgres bypassing stale L1.
func (c *WalletCache) Refresh(ctx context.Context, userID uuid.UUID) (WalletSnapshot, error) {
	c.Invalidate(ctx, userID)
	return c.loadL2OrDB(ctx, userID)
}
