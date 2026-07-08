package classifier

import (
	"testing"

	"polyforge/internal/telemetry"
)

func TestClassifyAICacheable(t *testing.T) {
	events := []telemetry.Event{
		{TenantID: "acme", PayloadBytes: 12000, EmbeddingDensity: 0.72, CacheHit: true, RPSWindow: 20},
		{TenantID: "acme", PayloadBytes: 11000, EmbeddingDensity: 0.7, CacheHit: false, RPSWindow: 24},
	}

	got := Classify("acme", events)
	if got.Label != AICacheable {
		t.Fatalf("expected %s, got %s", AICacheable, got.Label)
	}
}

func TestClassifyAgentic(t *testing.T) {
	events := []telemetry.Event{
		{TenantID: "acme", PayloadBytes: 9000, ChildSpans: 4, RPSWindow: 10},
		{TenantID: "acme", PayloadBytes: 8500, ChildSpans: 3, RPSWindow: 12},
	}

	got := Classify("acme", events)
	if got.Label != AgenticMultistep {
		t.Fatalf("expected %s, got %s", AgenticMultistep, got.Label)
	}
}

func TestClassifyCRUDBursty(t *testing.T) {
	events := []telemetry.Event{
		{TenantID: "acme", PayloadBytes: 900, RPSWindow: 10},
		{TenantID: "acme", PayloadBytes: 950, RPSWindow: 120},
	}

	got := Classify("acme", events)
	if got.Label != CRUDBursty {
		t.Fatalf("expected %s, got %s", CRUDBursty, got.Label)
	}
}
