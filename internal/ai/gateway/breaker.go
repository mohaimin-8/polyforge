package gateway

import (
	"context"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// Breaker is the canary rollback decision, factored out of CanaryProvider so
// the decision can be either in-process (single gateway replica) or shared
// across replicas through Redis. The contract is identical for both:
//
//   - Observe records one canary outcome and returns whether the breaker is
//     now tripped.
//   - Tripped reports whether traffic should route to stable.
//   - Reset re-arms the breaker after a fix ships.
//
// A shared breaker is what makes canary rollback survive replication: once a
// bad canary trips the window on any replica, every replica routes back to
// stable instead of each independently re-discovering the failure.
type Breaker interface {
	Observe(ctx context.Context, failed bool) bool
	Tripped(ctx context.Context) bool
	Reset(ctx context.Context)
}

const (
	canaryWindowSize = 20
	canaryMinSample  = 5
	canaryTripRatio  = 0.5
)

// localBreaker is the original in-process rolling-window breaker: the ratio of
// failures over the last canaryWindowSize canary requests, tripping past
// canaryTripRatio once canaryMinSample results exist. It is the default and
// the same-process backstop the Redis breaker falls back to on any Redis error.
type localBreaker struct {
	mu      sync.Mutex
	window  []bool // ring buffer: true = the canary request failed
	next    int
	filled  int
	tripped bool
}

func newLocalBreaker() *localBreaker {
	return &localBreaker{window: make([]bool, canaryWindowSize)}
}

func (b *localBreaker) Observe(_ context.Context, failed bool) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.tripped {
		return true
	}
	b.window[b.next] = failed
	b.next = (b.next + 1) % len(b.window)
	if b.filled < len(b.window) {
		b.filled++
	}
	if b.filled < canaryMinSample {
		return false
	}
	failures := 0
	for i := 0; i < b.filled; i++ {
		if b.window[i] {
			failures++
		}
	}
	if float64(failures)/float64(b.filled) >= canaryTripRatio {
		b.tripped = true
	}
	return b.tripped
}

func (b *localBreaker) Tripped(_ context.Context) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.tripped
}

func (b *localBreaker) Reset(_ context.Context) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.tripped = false
	b.window = make([]bool, canaryWindowSize)
	b.next, b.filled = 0, 0
}

// breakerScript maintains the shared rolling window and trip flag atomically,
// so concurrent observes from many replicas can never race the count against
// the flag. KEYS: window list, trip flag. ARGV: failed(0|1), windowSize,
// minSample, tripRatio, ttlSeconds. Returns 1 when tripped, else 0.
var breakerScript = redis.NewScript(`
local window = KEYS[1]
local flag = KEYS[2]
local ttl = tonumber(ARGV[5])
if redis.call('GET', flag) == '1' then
  return 1
end
redis.call('LPUSH', window, ARGV[1])
redis.call('LTRIM', window, 0, tonumber(ARGV[2]) - 1)
redis.call('EXPIRE', window, ttl)
local vals = redis.call('LRANGE', window, 0, -1)
local filled = #vals
if filled < tonumber(ARGV[3]) then
  return 0
end
local failures = 0
for i = 1, filled do
  if vals[i] == '1' then failures = failures + 1 end
end
if failures / filled >= tonumber(ARGV[4]) then
  redis.call('SET', flag, '1', 'EX', ttl)
  return 1
end
return 0
`)

// redisBreaker shares the trip decision across gateway replicas. It keeps a
// same-process localBreaker as both a fast path (Tripped avoids a Redis round
// trip when already locally tripped) and a fail-safe (any Redis error falls
// back to the local window, so a Redis blip degrades to today's behavior
// rather than dropping the safety mechanism). The effective decision is
// "tripped locally OR tripped in the shared window", which trips at least as
// fast as the in-process breaker and additionally propagates across replicas.
type redisBreaker struct {
	client     redis.UniversalClient
	windowKey  string
	flagKey    string
	ttl        time.Duration
	local      *localBreaker
	refreshTTL time.Duration

	mu          sync.Mutex
	cachedTrip  bool
	lastChecked time.Time
	now         func() time.Time
}

func newRedisBreaker(client redis.UniversalClient, keyPrefix string) *redisBreaker {
	// Hash-tag the keys so both live in the same slot under Redis Cluster,
	// which the Lua script requires (multi-key on one node).
	tag := "{canary:" + keyPrefix + "}"
	return &redisBreaker{
		client:     client,
		windowKey:  tag + ":window",
		flagKey:    tag + ":trip",
		ttl:        10 * time.Minute,
		local:      newLocalBreaker(),
		refreshTTL: time.Second, // bound shared-state staleness on the fast path
		now:        time.Now,
	}
}

func (b *redisBreaker) Observe(ctx context.Context, failed bool) bool {
	localTrip := b.local.Observe(ctx, failed)
	arg := "0"
	if failed {
		arg = "1"
	}
	res, err := breakerScript.Run(ctx, b.client,
		[]string{b.windowKey, b.flagKey},
		arg, canaryWindowSize, canaryMinSample, canaryTripRatio,
		int(b.ttl.Seconds()),
	).Int64()
	if err != nil {
		return localTrip // fail safe: same-process decision still stands
	}
	shared := res == 1
	b.setCache(shared)
	return localTrip || shared
}

func (b *redisBreaker) Tripped(ctx context.Context) bool {
	if b.local.Tripped(ctx) {
		return true
	}
	b.mu.Lock()
	fresh := b.now().Sub(b.lastChecked) < b.refreshTTL
	cached := b.cachedTrip
	b.mu.Unlock()
	if fresh {
		return cached
	}
	res, err := b.client.Get(ctx, b.flagKey).Result()
	if err != nil {
		// Missing key (redis.Nil) means "not tripped"; a real error falls
		// back to the last cached shared value, never crashing the request.
		if err == redis.Nil {
			b.setCache(false)
			return false
		}
		return cached
	}
	shared := res == "1"
	b.setCache(shared)
	return shared
}

func (b *redisBreaker) Reset(ctx context.Context) {
	b.local.Reset(ctx)
	b.client.Del(ctx, b.windowKey, b.flagKey)
	b.setCache(false)
}

func (b *redisBreaker) setCache(v bool) {
	b.mu.Lock()
	b.cachedTrip = v
	b.lastChecked = b.now()
	b.mu.Unlock()
}
