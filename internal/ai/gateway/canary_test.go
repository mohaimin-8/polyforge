package gateway_test

import (
	"context"
	"errors"
	"testing"

	"polyforge/internal/ai/gateway"
)

// failingProvider errors on every call; used as a broken canary.
type failingProvider struct{ calls int }

func (p *failingProvider) Chat(_ context.Context, _ gateway.ChatRequest) (gateway.ChatResponse, error) {
	p.calls++
	return gateway.ChatResponse{}, errors.New("v2 is broken")
}

func (p *failingProvider) StreamChat(_ context.Context, _ gateway.ChatRequest, _ func(string) error) (gateway.ChatResponse, error) {
	p.calls++
	return gateway.ChatResponse{}, errors.New("v2 is broken")
}

func TestCanarySplitsTrafficByWeight(t *testing.T) {
	stable := namedProvider("v1")
	canaryTarget := namedProvider("v2")
	canary := gateway.NewCanaryProvider(stable, canaryTarget, 5)

	req := gateway.ChatRequest{Messages: []gateway.Message{{Role: "user", Content: "x"}}}
	for i := 0; i < 200; i++ {
		if _, err := canary.Chat(t.Context(), req); err != nil {
			t.Fatalf("chat %d: %v", i, err)
		}
	}
	// Deterministic split: exactly 5 per 100 requests go to the canary.
	if canaryTarget.calls != 10 {
		t.Fatalf("canary calls = %d, want 10 of 200 at 5%%", canaryTarget.calls)
	}
	if stable.calls != 190 {
		t.Fatalf("stable calls = %d, want 190", stable.calls)
	}
	if canary.Tripped() {
		t.Fatal("healthy canary must not trip the breaker")
	}
}

// TestCanaryErrorSpikeAutoRollsBack is the W22 verification gate: an error
// spike on v2 shifts traffic back to v1 without operator action.
func TestCanaryErrorSpikeAutoRollsBack(t *testing.T) {
	stable := namedProvider("v1")
	broken := &failingProvider{}
	canary := gateway.NewCanaryProvider(stable, broken, 50)

	req := gateway.ChatRequest{Messages: []gateway.Message{{Role: "user", Content: "x"}}}
	for i := 0; i < 100; i++ {
		_, _ = canary.Chat(t.Context(), req)
	}
	if !canary.Tripped() {
		t.Fatal("breaker did not trip on an always-failing canary")
	}
	brokenCallsAtTrip := broken.calls
	if brokenCallsAtTrip >= 20 {
		t.Fatalf("breaker took %d canary failures to trip, want < 20", brokenCallsAtTrip)
	}

	// After the trip every request succeeds via stable.
	for i := 0; i < 50; i++ {
		if _, err := canary.Chat(t.Context(), req); err != nil {
			t.Fatalf("post-trip chat %d failed: %v", i, err)
		}
	}
	if broken.calls != brokenCallsAtTrip {
		t.Fatalf("canary still receiving traffic after trip: %d -> %d", brokenCallsAtTrip, broken.calls)
	}

	// Reset re-arms the canary for the fixed deploy.
	canary.Reset()
	if canary.Tripped() {
		t.Fatal("reset did not clear the breaker")
	}
}

func TestCanaryZeroWeightNeverRoutesToCanary(t *testing.T) {
	stable := namedProvider("v1")
	canaryTarget := namedProvider("v2")
	canary := gateway.NewCanaryProvider(stable, canaryTarget, 0)

	req := gateway.ChatRequest{Messages: []gateway.Message{{Role: "user", Content: "x"}}}
	for i := 0; i < 50; i++ {
		if _, err := canary.Chat(t.Context(), req); err != nil {
			t.Fatal(err)
		}
	}
	if canaryTarget.calls != 0 {
		t.Fatalf("canary calls = %d, want 0 at weight 0", canaryTarget.calls)
	}
}
