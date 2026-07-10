package gateway

import (
	"context"
	"fmt"
	"testing"

	"polyforge/internal/ai/embed"
)

func prompt(text string) []Message {
	return []Message{{Role: "user", Content: text}}
}

func TestBoundedCacheEnforcesPerTenantCapacity(t *testing.T) {
	cache := NewSemanticCache(embed.NewLocal(128), 0.95).WithCapacity(2)
	ctx := context.Background()
	for i := 0; i < 6; i++ {
		text := fmt.Sprintf("wholly distinct prompt number %d about topic %d", i, i*7)
		cache.StoreWithCost(ctx, "acme", prompt(text), "completion", 0.001)
	}
	if got := cache.index.Count("acme"); got != 2 {
		t.Fatalf("resident entries = %d, want capacity 2", got)
	}
}

func TestBoundedCacheKeepsExpensiveEntry(t *testing.T) {
	cache := NewSemanticCache(embed.NewLocal(128), 0.95).WithCapacity(2)
	ctx := context.Background()
	dear := prompt("explain the proof of the four color theorem in detail")
	cache.StoreWithCost(ctx, "acme", dear, "long expensive completion", 0.5)
	cheap := prompt("what is two plus two")
	cache.StoreWithCost(ctx, "acme", cheap, "4", 0.00001)
	third := prompt("summarize the plot of hamlet for a school essay")
	cache.StoreWithCost(ctx, "acme", third, "completion", 0.001)

	if _, _, ok := cache.Lookup(ctx, "acme", dear); !ok {
		t.Fatal("the 500x more expensive entry must survive eviction")
	}
	if _, _, ok := cache.Lookup(ctx, "acme", cheap); ok {
		t.Fatal("the cheapest entry should have been the victim")
	}
}

func TestBoundedCacheIsolatesTenantCapacity(t *testing.T) {
	cache := NewSemanticCache(embed.NewLocal(128), 0.95).WithCapacity(2)
	ctx := context.Background()
	victim := prompt("globex tenant's only cached prompt")
	cache.StoreWithCost(ctx, "globex", victim, "completion", 0.0001)
	for i := 0; i < 10; i++ {
		text := fmt.Sprintf("acme flood prompt %d topic %d", i, i*13)
		cache.StoreWithCost(ctx, "acme", prompt(text), "completion", 0.9)
	}
	if _, _, ok := cache.Lookup(ctx, "globex", victim); !ok {
		t.Fatal("acme's flood evicted globex's entry: capacity must be per-tenant")
	}
}

// TestCacheGivesNoCrossTenantHit is the read-isolation invariant behind the
// cross-tenant timing side-channel study (research/security): one tenant's
// entry must never produce a hit for another tenant probing the *identical*
// prompt. A shared cache would return a fast hit here and leak, via response
// time alone, that some other tenant recently asked this question. The
// per-tenant index closes that channel; this test is what keeps it closed.
func TestCacheGivesNoCrossTenantHit(t *testing.T) {
	cache := NewSemanticCache(embed.NewLocal(128), 0.95)
	ctx := context.Background()
	secret := prompt("victim's confidential prompt about an unreleased product")
	cache.StoreWithCost(ctx, "victim", secret, "the sensitive completion", 0.01)

	// The victim, of course, hits its own entry.
	if _, _, ok := cache.Lookup(ctx, "victim", secret); !ok {
		t.Fatal("victim must hit its own cached prompt")
	}
	// The attacker probes the byte-identical prompt and must miss: no hit,
	// no borrowed completion, nothing but a cold lookup.
	if completion, _, ok := cache.Lookup(ctx, "attacker", secret); ok {
		t.Fatalf("cross-tenant hit leaked (completion=%q): the timing side channel is open", completion)
	}
}

func TestUnboundedCacheKeepsCompatibleBehavior(t *testing.T) {
	cache := NewSemanticCache(embed.NewLocal(128), 0.95)
	ctx := context.Background()
	for i := 0; i < 20; i++ {
		cache.Store(ctx, "acme", prompt(fmt.Sprintf("prompt %d topic %d", i, i*3)), "completion")
	}
	if got := cache.index.Count("acme"); got != 20 {
		t.Fatalf("unbounded cache dropped entries: %d of 20", got)
	}
}
