package idempotency

import (
	"context"
	"net/http"
	"testing"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

// Both stores must satisfy the same claim → complete → replay contract.
func TestStoreContract(t *testing.T) {
	redisServer := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: redisServer.Addr()})
	t.Cleanup(func() { _ = client.Close() })

	stores := map[string]Store{
		"memory": NewMemoryStore(),
		"redis":  NewRedisStore(client),
	}
	for name, store := range stores {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()

			cached, inFlight, err := store.Begin(ctx, "key-1")
			if err != nil || cached != nil || inFlight {
				t.Fatalf("first Begin must claim: cached=%v inFlight=%v err=%v", cached, inFlight, err)
			}

			// A duplicate while the first holds the claim is in flight.
			if _, inFlight, err = store.Begin(ctx, "key-1"); err != nil || !inFlight {
				t.Fatalf("concurrent Begin must report in-flight: inFlight=%v err=%v", inFlight, err)
			}

			response := CachedResponse{Status: http.StatusCreated, ContentType: "application/json", Body: []byte(`{"id":"p1"}`)}
			if err := store.Complete(ctx, "key-1", response); err != nil {
				t.Fatal(err)
			}
			cached, inFlight, err = store.Begin(ctx, "key-1")
			if err != nil || inFlight || cached == nil {
				t.Fatalf("Begin after Complete must replay: cached=%v inFlight=%v err=%v", cached, inFlight, err)
			}
			if cached.Status != http.StatusCreated || string(cached.Body) != `{"id":"p1"}` {
				t.Fatalf("cached response corrupted: %+v", cached)
			}

			// Abandon releases the claim so a retry executes fresh.
			if _, _, err := store.Begin(ctx, "key-2"); err != nil {
				t.Fatal(err)
			}
			if err := store.Abandon(ctx, "key-2"); err != nil {
				t.Fatal(err)
			}
			if cached, inFlight, err := store.Begin(ctx, "key-2"); err != nil || cached != nil || inFlight {
				t.Fatalf("Begin after Abandon must claim fresh: cached=%v inFlight=%v err=%v", cached, inFlight, err)
			}
		})
	}
}
