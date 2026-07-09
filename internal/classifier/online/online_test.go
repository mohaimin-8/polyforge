package online

import (
	"context"
	"io"
	"log/slog"
	"math/rand"
	"sync"
	"testing"
	"time"

	"polyforge/internal/classifier"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

type capturePublisher struct {
	mu       sync.Mutex
	subjects []string
}

func (c *capturePublisher) Publish(_ context.Context, subject, _ string, _ []byte) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.subjects = append(c.subjects, subject)
	return nil
}

func (c *capturePublisher) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.subjects)
}

func discard() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func seedTenant(t *testing.T, tenants *tenant.Store, telemetryStore *telemetry.Store, id string, label classifier.Label, count int) {
	t.Helper()
	ctx := context.Background()
	if _, err := tenants.CreateTenant(ctx, tenant.Tenant{ID: id, Name: id}); err != nil {
		t.Fatal(err)
	}
	rng := rand.New(rand.NewSource(int64(len(id))))
	window := classifier.SyntheticWindow(rng, label)
	for i := 0; i < count && i < len(window); i++ {
		event := window[i]
		event.TenantID = id
		if _, err := telemetryStore.Add(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
}

func trainedPredictor(t *testing.T) Predictor {
	t.Helper()
	train := classifier.SyntheticExamples(rand.New(rand.NewSource(1)), 80)
	model, err := classifier.Train(train, 300, 2.0, rand.New(rand.NewSource(3)))
	if err != nil {
		t.Fatal(err)
	}
	return model
}

func TestTickClassifiesAndPublishesOnlyChanges(t *testing.T) {
	tenants := tenant.NewStore()
	telemetryStore := telemetry.NewStore(1000)
	seedTenant(t, tenants, telemetryStore, "acme", classifier.AgenticMultistep, 60)
	publisher := &capturePublisher{}
	registry := NewRegistry(0)
	poller := NewPoller(tenants, telemetryStore, trainedPredictor(t), nil, publisher, registry, discard(), Config{})

	ctx := context.Background()
	poller.Tick(ctx)
	if publisher.count() != 1 {
		t.Fatalf("first observation should publish once, got %d", publisher.count())
	}
	snapshot, _ := registry.Snapshot()
	if len(snapshot) != 1 || snapshot[0].Label != classifier.AgenticMultistep {
		t.Fatalf("snapshot = %+v", snapshot)
	}

	// Stable label across further ticks: no new publishes.
	poller.Tick(ctx)
	poller.Tick(ctx)
	if publisher.count() != 1 {
		t.Fatalf("unchanged label republished: %d", publisher.count())
	}
	if got := len(registry.History("acme")); got != 3 {
		t.Fatalf("history samples = %d, want 3", got)
	}
}

func TestTickFallsBackToRulesWhenModelAbstains(t *testing.T) {
	tenants := tenant.NewStore()
	telemetryStore := telemetry.NewStore(1000)
	seedTenant(t, tenants, telemetryStore, "acme", classifier.AgenticMultistep, 60)
	registry := NewRegistry(0)
	poller := NewPoller(tenants, telemetryStore, RulePredictor{}, nil, nil, registry, discard(), Config{})
	poller.Tick(context.Background())
	snapshot, _ := registry.Snapshot()
	if len(snapshot) != 1 || snapshot[0].Label != classifier.AgenticMultistep {
		t.Fatalf("rules fallback snapshot = %+v", snapshot)
	}
}

func TestSingleClassPopulationTripsDriftAgainstBalancedReference(t *testing.T) {
	reference := classifier.SyntheticExamples(rand.New(rand.NewSource(11)), 100)
	detector, err := classifier.NewDriftDetector(reference)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	telemetryStore := telemetry.NewStore(5000)
	for _, id := range []string{"a1", "a2", "a3", "a4"} {
		seedTenant(t, tenants, telemetryStore, id, classifier.CRUDSteady, 60)
	}
	registry := NewRegistry(0)
	poller := NewPoller(tenants, telemetryStore, trainedPredictor(t), detector, nil, registry, discard(), Config{DriftWindow: 8})
	ctx := context.Background()
	poller.Tick(ctx)
	poller.Tick(ctx)
	_, drift := registry.Snapshot()
	if !drift.Drifted {
		t.Fatalf("all-CRUD population vs balanced reference must drift, got %+v", drift)
	}
	if drift.WindowSize == 0 || drift.CheckedAt.IsZero() {
		t.Fatalf("drift status incomplete: %+v", drift)
	}
}

func TestRegistryHistoryIsBounded(t *testing.T) {
	registry := NewRegistry(3)
	at := time.Now()
	for i := 0; i < 5; i++ {
		registry.record("acme", classifier.CRUDSteady, 0.9, 10, at.Add(time.Duration(i)*time.Second))
	}
	if got := len(registry.History("acme")); got != 3 {
		t.Fatalf("history = %d, want cap 3", got)
	}
}

func TestEmptyTelemetryTenantsAreSkipped(t *testing.T) {
	tenants := tenant.NewStore()
	telemetryStore := telemetry.NewStore(100)
	if _, err := tenants.CreateTenant(context.Background(), tenant.Tenant{ID: "quiet", Name: "Quiet"}); err != nil {
		t.Fatal(err)
	}
	registry := NewRegistry(0)
	poller := NewPoller(tenants, telemetryStore, RulePredictor{}, nil, nil, registry, discard(), Config{})
	poller.Tick(context.Background())
	snapshot, _ := registry.Snapshot()
	if len(snapshot) != 0 {
		t.Fatalf("tenant with no telemetry classified: %+v", snapshot)
	}
}
