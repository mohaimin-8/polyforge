// Package limit implements the W13 sliding-window rate limiter: a single
// Lua script over a Redis sorted set, so admission is atomic and accurate to
// the millisecond with no check-then-act race. The in-process token bucket
// in internal/platform remains the fallback when Redis is not configured;
// this limiter is the one that stays correct across replicas, because every
// control-plane pod shares the same Redis window.
package limit

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// script admits or rejects one request against a per-key sliding window.
// KEYS[1] window sorted set; ARGV: now-micros, window-micros, limit, member.
// Returns {allowed 0|1, retry-after-micros}.
var script = redis.NewScript(`
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, ARGV[4])
  redis.call('PEXPIRE', key, math.ceil(window / 1000) + 1000)
  return {1, 0}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry = window
if oldest[2] then
  retry = tonumber(oldest[2]) + window - now
end
return {0, retry}
`)

// SlidingWindow admits at most Limit requests per Window per key.
type SlidingWindow struct {
	client redis.UniversalClient
	limit  int
	window time.Duration
	now    func() time.Time
}

func NewSlidingWindow(client redis.UniversalClient, limit int, window time.Duration) *SlidingWindow {
	return &SlidingWindow{client: client, limit: limit, window: window, now: time.Now}
}

// Allow reports whether the request identified by key may proceed, and if
// not, how long the caller should wait before the oldest window entry
// expires. A Redis error is returned to the caller — the policy decision
// (fail open or closed) belongs to the HTTP layer, not here.
func (l *SlidingWindow) Allow(ctx context.Context, key string) (bool, time.Duration, error) {
	member := make([]byte, 8)
	if _, err := rand.Read(member); err != nil {
		return false, 0, fmt.Errorf("generate window member: %w", err)
	}
	now := l.now().UnixMicro()
	result, err := script.Run(ctx, l.client,
		[]string{"ratelimit:{" + key + "}"},
		now, l.window.Microseconds(), l.limit, hex.EncodeToString(member),
	).Int64Slice()
	if err != nil {
		return false, 0, fmt.Errorf("run sliding-window script: %w", err)
	}
	if len(result) != 2 {
		return false, 0, fmt.Errorf("sliding-window script returned %d values", len(result))
	}
	if result[0] == 1 {
		return true, 0, nil
	}
	return false, time.Duration(result[1]) * time.Microsecond, nil
}

// ScriptHash exposes the SHA1 go-redis registers via EVALSHA, so tests can
// prove the script is cached server-side (the roadmap's SCRIPT EXISTS gate).
func ScriptHash() string {
	return script.Hash()
}
