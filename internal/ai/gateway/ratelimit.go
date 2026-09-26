package gateway

import (
	"math"
	"sync"
	"time"
)

// The AI gateway had no admission control of any kind: no per-tenant budget,
// no global cap. A single authenticated tenant — or any leaked/compromised
// tenant key, both in the threat model — could flood /ai/chat or /ai/agent
// (the agent path runs a multi-step LLM + tool loop per request, the most
// expensive call in the system) and exhaust the shared model backend and CPU
// for every other tenant. This is a per-tenant in-process token bucket in
// front of every AI route, mirroring the control plane's own default limiter
// (internal/platform, RPM=600/Burst=60). It is a floor, not a substitute for
// the per-tenant LLM budget the router already enforces on spend.

// RateLimit configures the gateway's per-tenant token bucket. Zero on either
// field disables limiting (used by tests that assert other behavior).
type RateLimit struct {
	RequestsPerMinute int
	Burst             int
}

// DefaultRateLimit matches the control plane's default admission budget.
var DefaultRateLimit = RateLimit{RequestsPerMinute: 600, Burst: 60}

type tokenBucket struct {
	tokens   float64
	updated  time.Time
	lastSeen time.Time
}

type tenantLimiter struct {
	mu              sync.Mutex
	buckets         map[string]*tokenBucket
	capacity        float64
	refillPerSecond float64
	clock           func() time.Time
	lastPrune       time.Time
}

func newTenantLimiter(cfg RateLimit) *tenantLimiter {
	if cfg.RequestsPerMinute <= 0 || cfg.Burst <= 0 {
		return nil
	}
	return &tenantLimiter{
		buckets:         make(map[string]*tokenBucket),
		capacity:        float64(cfg.Burst),
		refillPerSecond: float64(cfg.RequestsPerMinute) / 60,
		clock:           time.Now,
	}
}

// allow consumes one token for key, returning whether the request may proceed
// and, if not, how long until the next token.
func (l *tenantLimiter) allow(key string) (bool, time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	b := l.refilled(key, l.clock())
	if b.tokens < 1 {
		return false, l.waitFor(b)
	}
	b.tokens--
	return true, 0
}

// refund returns one token to key's bucket, never past capacity. The
// gateway's failed-authentication budget is RESERVED with allow before a key
// lookup and refunded when the lookup did not fail on the caller's
// credential: reserving first caps a concurrent bad-key burst at the
// bucket's capacity the moment it arrives, where checking without reserving
// let every concurrent request through to the key store (security review
// 2026-09-26).
func (l *tenantLimiter) refund(key string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	b := l.refilled(key, l.clock())
	b.tokens = math.Min(l.capacity, b.tokens+1)
}

// refilled returns key's bucket topped up to now. Callers hold l.mu.
func (l *tenantLimiter) refilled(key string, now time.Time) *tokenBucket {
	// Reclaim idle buckets so the map does not grow for the process
	// lifetime; keys are tenant ids or client addresses, and a churny fleet
	// (or a scanner rotating addresses) still accumulates them.
	if l.lastPrune.IsZero() {
		l.lastPrune = now
	} else if now.Sub(l.lastPrune) > 10*time.Minute {
		for k, b := range l.buckets {
			if now.Sub(b.lastSeen) > 10*time.Minute {
				delete(l.buckets, k)
			}
		}
		l.lastPrune = now
	}
	b := l.buckets[key]
	if b == nil {
		b = &tokenBucket{tokens: l.capacity, updated: now}
		l.buckets[key] = b
	}
	b.tokens = math.Min(l.capacity, b.tokens+now.Sub(b.updated).Seconds()*l.refillPerSecond)
	b.updated = now
	b.lastSeen = now
	return b
}

func (l *tenantLimiter) waitFor(b *tokenBucket) time.Duration {
	return time.Duration((1 - b.tokens) / l.refillPerSecond * float64(time.Second))
}

// forget drops a key's bucket. Nothing outside the tests calls it today: a
// deleted tenant's bucket is reclaimed by the idle prune in refilled. The
// admission checks themselves live in Server.authorize (server.go), the
// shared choke point for every AI route, because r.PathValue -- the tenant
// key -- is only populated after the mux routes the request, so wrapping the
// mux would see an empty tenant.
func (l *tenantLimiter) forget(key string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.buckets, key)
}
