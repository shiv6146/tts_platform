package auth

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"golang.org/x/crypto/bcrypt"
)

const APIKeyPrefix = "sk-"

type User struct {
	ID       uuid.UUID
	Username string
}

type APIKeyRecord struct {
	ID       uuid.UUID
	UserID   uuid.UUID
	Prefix   string
	KeyHash  string
	Revoked  bool
}

func HashPassword(password string) (string, error) {
	b, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

func CheckPassword(hash, password string) bool {
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(password)) == nil
}

func HashAPIKey(secret string) string {
	sum := sha256.Sum256([]byte(secret))
	return hex.EncodeToString(sum[:])
}

func GenerateAPIKey() (prefix, secret string, err error) {
	var raw [24]byte
	if _, err = rand.Read(raw[:]); err != nil {
		return "", "", err
	}
	body := hex.EncodeToString(raw[:])
	prefix = body[:8]
	secret = APIKeyPrefix + body
	return prefix, secret, nil
}

func Register(ctx context.Context, pool *pgxpool.Pool, username, password string, initialWalletUSD float64) (User, error) {
	hash, err := HashPassword(password)
	if err != nil {
		return User{}, err
	}
	var u User
	err = pool.QueryRow(ctx, `
		INSERT INTO users (username, password_hash)
		VALUES ($1, $2)
		RETURNING id, username`, username, hash).Scan(&u.ID, &u.Username)
	if err != nil {
		return User{}, err
	}
	_, err = pool.Exec(ctx, `INSERT INTO wallets (user_id, balance_usd) VALUES ($1, $2)`, u.ID, initialWalletUSD)
	return u, err
}

func AuthenticatePassword(ctx context.Context, pool *pgxpool.Pool, username, password string) (User, error) {
	var u User
	var hash string
	err := pool.QueryRow(ctx, `
		SELECT id, username, password_hash FROM users WHERE username = $1`, username).Scan(&u.ID, &u.Username, &hash)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return User{}, fmt.Errorf("invalid credentials")
		}
		return User{}, err
	}
	if !CheckPassword(hash, password) {
		return User{}, fmt.Errorf("invalid credentials")
	}
	return u, nil
}

func ResolveAPIKey(ctx context.Context, pool *pgxpool.Pool, bearer string) (User, error) {
	secret := strings.TrimSpace(bearer)
	if secret == "" {
		return User{}, fmt.Errorf("missing token")
	}
	if !strings.HasPrefix(secret, APIKeyPrefix) {
		secret = APIKeyPrefix + secret
	}
	if len(secret) < len(APIKeyPrefix)+8 {
		return User{}, fmt.Errorf("invalid key")
	}
	prefix := secret[len(APIKeyPrefix) : len(APIKeyPrefix)+8]
	keyHash := HashAPIKey(secret)

	var u User
	err := pool.QueryRow(ctx, `
		SELECT u.id, u.username
		FROM api_keys k
		JOIN users u ON u.id = k.user_id
		WHERE k.key_prefix = $1 AND k.key_hash = $2 AND k.revoked_at IS NULL`,
		prefix, keyHash).Scan(&u.ID, &u.Username)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return User{}, fmt.Errorf("invalid key")
		}
		return User{}, err
	}
	return u, nil
}

func CreateAPIKey(ctx context.Context, pool *pgxpool.Pool, userID uuid.UUID) (id uuid.UUID, prefix, secret string, err error) {
	prefix, secret, err = GenerateAPIKey()
	if err != nil {
		return uuid.Nil, "", "", err
	}
	keyHash := HashAPIKey(secret)
	err = pool.QueryRow(ctx, `
		INSERT INTO api_keys (user_id, key_prefix, key_hash)
		VALUES ($1, $2, $3)
		RETURNING id`, userID, prefix, keyHash).Scan(&id)
	return id, prefix, secret, err
}

func ListAPIKeys(ctx context.Context, pool *pgxpool.Pool, userID uuid.UUID) ([]APIKeyRecord, error) {
	rows, err := pool.Query(ctx, `
		SELECT id, user_id, key_prefix, key_hash, revoked_at IS NOT NULL
		FROM api_keys WHERE user_id = $1 ORDER BY created_at DESC`, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []APIKeyRecord
	for rows.Next() {
		var r APIKeyRecord
		if err := rows.Scan(&r.ID, &r.UserID, &r.Prefix, &r.KeyHash, &r.Revoked); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func RevokeAPIKey(ctx context.Context, pool *pgxpool.Pool, userID, keyID uuid.UUID) error {
	tag, err := pool.Exec(ctx, `
		UPDATE api_keys SET revoked_at = now()
		WHERE id = $1 AND user_id = $2 AND revoked_at IS NULL`, keyID, userID)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return pgx.ErrNoRows
	}
	return nil
}

func EnsureDefaultUser(ctx context.Context, pool *pgxpool.Pool, username, password string, walletUSD, pricePerMinute float64) (uuid.UUID, string, error) {
	var id uuid.UUID
	err := pool.QueryRow(ctx, `SELECT id FROM users WHERE username = $1`, username).Scan(&id)
	if err == nil {
		return id, "", nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return uuid.Nil, "", err
	}
	hash, err := HashPassword(password)
	if err != nil {
		return uuid.Nil, "", err
	}
	err = pool.QueryRow(ctx, `
		INSERT INTO users (username, password_hash, price_per_audio_minute_usd)
		VALUES ($1, $2, $3) RETURNING id`, username, hash, pricePerMinute).Scan(&id)
	if err != nil {
		return uuid.Nil, "", err
	}
	_, err = pool.Exec(ctx, `INSERT INTO wallets (user_id, balance_usd) VALUES ($1, $2)`, id, walletUSD)
	if err != nil {
		return uuid.Nil, "", err
	}
	_, prefix, secret, err := CreateAPIKey(ctx, pool, id)
	if err != nil {
		return id, "", err
	}
	_ = prefix
	return id, secret, nil
}
