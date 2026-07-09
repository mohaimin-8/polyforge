package limit

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

func newTestLimiter(t *testing.T, limit int, window time.Duration) (*SlidingWindow, *miniredis.Miniredis) {
	t.Helper()
	server := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: server.Addr()})
	t.Cleanup(func() { _ = client.Close() })
	return NewSlidingWindow(client, limit, window), server
}

func TestSlidingWindowHoldsExactLimit(t *testing.T) {
	limiter, _ := newTestLimiter(t, 5, time.Minute)
	ctx := context.Background()

	for i := 0; i < 5; i++ {
		ok, _, err := limiter.Allow(ctx, "tenant-a")
		if err != nil || !ok {
			t.Fatalf("request %d should be admitted: ok=%v err=%v", i+1, ok, err)
		}
	}
	ok, retryAfter, err := limiter.Allow(ctx, "tenant-a")
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatal("request 6 must be rejected")
	}
	if retryAfter <= 0 || retryAfter > time.Minute {
		t.Fatalf("retry-after out of range: %v", retryAfter)
	}

	// Another key has an independent window.
	if ok, _, err := limiter.Allow(ctx, "tenant-b"); err != nil || !ok {
		t.Fatalf("independent key should be admitted: ok=%v err=%v", ok, err)
	}
}

func TestSlidingWindowSlides(t *testing.T) {
	limiter, _ := newTestLimiter(t, 2, time.Minute)
	ctx := context.Background()

	base := time.Now()
	limiter.now = func() time.Time { return base }
	for i := 0; i < 2; i++ {
		if ok, _, err := limiter.Allow(ctx, "k"); err != nil || !ok {
			t.Fatalf("seed request %d rejected: %v", i+1, err)
		}
	}
	if ok, _, _ := limiter.Allow(ctx, "k"); ok {
		t.Fatal("third request inside the window must be rejected")
	}

	// 30s later the window still holds both entries; 61s later it is empty.
	limiter.now = func() time.Time { return base.Add(30 * time.Second) }
	if ok, _, _ := limiter.Allow(ctx, "k"); ok {
		t.Fatal("request at +30s must still be rejected")
	}
	limiter.now = func() time.Time { return base.Add(61 * time.Second) }
	if ok, _, err := limiter.Allow(ctx, "k"); err != nil || !ok {
		t.Fatalf("request after the window slid must be admitted: %v", err)
	}
}

// TestSlidingWindowIsAtomicUnderConcurrency is the W13 gate: the limiter
// holds at exactly the configured limit under concurrent clients, because
// admission happens inside one Lua script rather than a get-then-set.
func TestSlidingWindowIsAtomicUnderConcurrency(t *testing.T) {
	const limit, attempts = 25, 200
	limiter, _ := newTestLimiter(t, limit, time.Minute)

	var admitted atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < attempts; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, _, err := limiter.Allow(context.Background(), "shared")
			if err != nil {
				t.Error(err)
				return
			}
			if ok {
				admitted.Add(1)
			}
		}()
	}
	wg.Wait()
	if got := admitted.Load(); got != limit {
		t.Fatalf("expected exactly %d admissions, got %d", limit, got)
	}
}

func TestScriptIsCachedServerSide(t *testing.T) {
	limiter, server := newTestLimiter(t, 1, time.Minute)
	if _, _, err := limiter.Allow(context.Background(), "k"); err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(&redis.Options{Addr: server.Addr()})
	defer func() { _ = client.Close() }()
	exists, err := client.ScriptExists(context.Background(), ScriptHash()).Result()
	if err != nil {
		t.Fatal(err)
	}
	if len(exists) != 1 || !exists[0] {
		t.Fatal("sliding-window script must be resident in the Redis script cache")
	}
}
