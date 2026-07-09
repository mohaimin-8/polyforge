// Package idempotency implements the W14 repeat-safe write pattern: the
// client sends an Idempotency-Key header, the first request claims the key
// with an atomic guard (Redis SETNX in production), and the completed
// response is cached so a retry returns the original outcome instead of
// executing the write twice.
package idempotency

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// TTL bounds how long a completed response is replayable. 24h matches the
// retry horizon of a client recovering from an outage; after that a reused
// key executes as a fresh request.
const TTL = 24 * time.Hour

// pendingMarker occupies the key between claim and completion so concurrent
// duplicates are detectable.
const pendingMarker = "pending"

// CachedResponse is the replayable outcome of a completed request.
type CachedResponse struct {
	Status      int    `json:"status"`
	ContentType string `json:"content_type"`
	Body        []byte `json:"body"`
}

// Store guards and caches idempotent requests.
type Store interface {
	// Begin claims key. Exactly one of three outcomes: (nil, false) — the
	// caller owns the key and must Complete or Abandon it; (resp, false) —
	// a finished response exists, replay it; (nil, true) — another request
	// holds the key right now.
	Begin(ctx context.Context, key string) (cached *CachedResponse, inFlight bool, err error)
	// Complete stores the response for replay and releases the claim.
	Complete(ctx context.Context, key string, resp CachedResponse) error
	// Abandon releases the claim without caching, so the client may retry.
	Abandon(ctx context.Context, key string) error
}

// RedisStore implements Store on Redis, giving all replicas one view of
// in-flight and completed keys.
type RedisStore struct {
	client redis.UniversalClient
}

func NewRedisStore(client redis.UniversalClient) *RedisStore {
	return &RedisStore{client: client}
}

func (s *RedisStore) Begin(ctx context.Context, key string) (*CachedResponse, bool, error) {
	claimed, err := s.client.SetNX(ctx, redisKey(key), pendingMarker, TTL).Result()
	if err != nil {
		return nil, false, fmt.Errorf("claim idempotency key: %w", err)
	}
	if claimed {
		return nil, false, nil
	}
	value, err := s.client.Get(ctx, redisKey(key)).Result()
	if errors.Is(err, redis.Nil) {
		// Claim expired or was abandoned between SETNX and GET; treat the
		// request as in flight and let the client retry shortly.
		return nil, true, nil
	}
	if err != nil {
		return nil, false, fmt.Errorf("read idempotency key: %w", err)
	}
	if value == pendingMarker {
		return nil, true, nil
	}
	var cached CachedResponse
	if err := json.Unmarshal([]byte(value), &cached); err != nil {
		return nil, false, fmt.Errorf("decode cached response: %w", err)
	}
	return &cached, false, nil
}

func (s *RedisStore) Complete(ctx context.Context, key string, resp CachedResponse) error {
	encoded, err := json.Marshal(resp)
	if err != nil {
		return fmt.Errorf("encode cached response: %w", err)
	}
	if err := s.client.Set(ctx, redisKey(key), encoded, TTL).Err(); err != nil {
		return fmt.Errorf("store cached response: %w", err)
	}
	return nil
}

func (s *RedisStore) Abandon(ctx context.Context, key string) error {
	if err := s.client.Del(ctx, redisKey(key)).Err(); err != nil {
		return fmt.Errorf("release idempotency key: %w", err)
	}
	return nil
}

func redisKey(key string) string {
	return "idem:{" + key + "}"
}

// MemoryStore implements Store in process for deployments without Redis
// (which are single-replica anyway, so a local map gives the same guarantee).
type MemoryStore struct {
	mu      sync.Mutex
	entries map[string]memoryEntry
	now     func() time.Time
}

type memoryEntry struct {
	cached    *CachedResponse // nil while pending
	expiresAt time.Time
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{entries: make(map[string]memoryEntry), now: time.Now}
}

func (s *MemoryStore) Begin(_ context.Context, key string) (*CachedResponse, bool, error) {
	now := s.now()
	s.mu.Lock()
	defer s.mu.Unlock()
	entry, ok := s.entries[key]
	if ok && now.Before(entry.expiresAt) {
		if entry.cached == nil {
			return nil, true, nil
		}
		return entry.cached, false, nil
	}
	s.entries[key] = memoryEntry{expiresAt: now.Add(TTL)}
	if len(s.entries) > 100_000 {
		s.pruneLocked(now)
	}
	return nil, false, nil
}

func (s *MemoryStore) Complete(_ context.Context, key string, resp CachedResponse) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.entries[key] = memoryEntry{cached: &resp, expiresAt: s.now().Add(TTL)}
	return nil
}

func (s *MemoryStore) Abandon(_ context.Context, key string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.entries, key)
	return nil
}

func (s *MemoryStore) pruneLocked(now time.Time) {
	for key, entry := range s.entries {
		if !now.Before(entry.expiresAt) {
			delete(s.entries, key)
		}
	}
}
