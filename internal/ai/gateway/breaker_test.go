package gateway

import (
	"context"
	"testing"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

func newSharedBreaker(t *testing.T, name string) (*redisBreaker, *miniredis.Miniredis) {
	t.Helper()
	server := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: server.Addr()})
	t.Cleanup(func() { _ = client.Close() })
	return newRedisBreaker(client, name), server
}

// The shared breaker must trip on the same failure ratio as the local one.
func TestSharedBreakerTripsOnErrorRatio(t *testing.T) {
	b, _ := newSharedBreaker(t, "v2")
	ctx := context.Background()
	tripped := false
	for i := 0; i < canaryWindowSize; i++ {
		if b.Observe(ctx, true) {
			tripped = true
			break
		}
	}
	if !tripped {
		t.Fatal("all-failing canary never tripped the shared breaker")
	}
	if !b.Tripped(ctx) {
		t.Fatal("Tripped disagrees with Observe")
	}
}

// The whole point: a trip observed by one replica is visible to another
// replica sharing the same Redis key, before that replica has enough of its
// own observations to trip locally.
func TestSharedBreakerPropagatesAcrossReplicas(t *testing.T) {
	server := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: server.Addr()})
	t.Cleanup(func() { _ = client.Close() })
	ctx := context.Background()

	replicaA := newRedisBreaker(client, "v2")
	replicaB := newRedisBreaker(client, "v2")

	// Replica A sees the whole failing window and trips.
	for i := 0; i < canaryWindowSize; i++ {
		replicaA.Observe(ctx, true)
	}
	if !replicaA.Tripped(ctx) {
		t.Fatal("replica A did not trip")
	}
	// Replica B has observed nothing of its own, yet must see the shared trip.
	if !replicaB.Tripped(ctx) {
		t.Fatal("replica B did not see the shared trip decision")
	}
}

// Reset on any replica clears the shared decision for all.
func TestSharedBreakerResetIsShared(t *testing.T) {
	server := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: server.Addr()})
	t.Cleanup(func() { _ = client.Close() })
	ctx := context.Background()

	a := newRedisBreaker(client, "v2")
	b := newRedisBreaker(client, "v2")
	for i := 0; i < canaryWindowSize; i++ {
		a.Observe(ctx, true)
	}
	a.Reset(ctx)
	if a.Tripped(ctx) {
		t.Fatal("reset did not clear the resetting replica")
	}
	// b still has a stale local cache; force a shared re-read past the throttle.
	b.mu.Lock()
	b.lastChecked = b.lastChecked.Add(-b.refreshTTL * 2)
	b.mu.Unlock()
	if b.Tripped(ctx) {
		t.Fatal("reset did not clear the shared trip flag seen by the other replica")
	}
}

// A distributed low failure rate that no single replica would trip on must
// still trip once the shared window fills past the ratio.
func TestSharedBreakerAggregatesAcrossReplicas(t *testing.T) {
	server := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: server.Addr()})
	t.Cleanup(func() { _ = client.Close() })
	ctx := context.Background()

	replicas := []*redisBreaker{
		newRedisBreaker(client, "v2"),
		newRedisBreaker(client, "v2"),
		newRedisBreaker(client, "v2"),
	}
	// Round-robin all-failure across three replicas: no replica alone reaches
	// canaryMinSample quickly, but the shared window does.
	tripped := false
	for i := 0; i < canaryWindowSize && !tripped; i++ {
		if replicas[i%len(replicas)].Observe(ctx, true) {
			tripped = true
		}
	}
	if !tripped {
		t.Fatal("shared window did not aggregate failures across replicas")
	}
}

// On a Redis outage the breaker must fall back to the in-process window
// rather than dropping the safety mechanism or crashing the request path.
func TestSharedBreakerFailsSafeWhenRedisDown(t *testing.T) {
	b, server := newSharedBreaker(t, "v2")
	ctx := context.Background()
	server.Close() // Redis is now unreachable

	tripped := false
	for i := 0; i < canaryWindowSize; i++ {
		if b.Observe(ctx, true) { // local window still tallies failures
			tripped = true
			break
		}
	}
	if !tripped {
		t.Fatal("with Redis down the local fallback window must still trip")
	}
	// Tripped must not panic or block when Redis is unreachable.
	_ = b.Tripped(ctx)
}
