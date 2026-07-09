package saga_test

import (
	"context"
	"errors"
	"testing"

	"polyforge/internal/events"
	"polyforge/internal/saga"
	"polyforge/internal/tenant"
)

func TestOnboardingHappyPath(t *testing.T) {
	store := tenant.NewStore()
	states := saga.NewMemoryStateStore()
	ctx := t.Context()

	result, err := saga.Onboard(ctx, store, states, "sg-1", tenant.Tenant{ID: "acme", Name: "Acme"}, nil)
	if err != nil {
		t.Fatalf("onboard: %v", err)
	}
	if result.State.Status != saga.StatusCompleted {
		t.Fatalf("status = %s, want completed", result.State.Status)
	}
	if result.Key.Secret == "" {
		t.Fatal("bootstrap key secret missing from a fresh onboarding")
	}

	if _, ok, _ := store.Tenant(ctx, "acme"); !ok {
		t.Fatal("tenant was not created")
	}
	keys, _ := store.ListAPIKeys(ctx, "acme")
	if len(keys) != 1 || keys[0].Name != "bootstrap" {
		t.Fatalf("keys = %+v, want exactly one bootstrap key", keys)
	}

	pending, _ := store.Unpublished(ctx, 100)
	var actions []string
	for _, e := range pending {
		actions = append(actions, e.Action)
	}
	want := map[string]bool{
		events.ActionTenantCreated:   false,
		events.ActionAPIKeyCreated:   false,
		events.ActionPolicyDefaulted: false,
		events.ActionTenantOnboarded: false,
	}
	for _, a := range actions {
		if _, tracked := want[a]; tracked {
			want[a] = true
		}
	}
	for action, seen := range want {
		if !seen {
			t.Errorf("outbox is missing %s (got %v)", action, actions)
		}
	}
}

// failingStore fails CreateAPIKey until allowed, simulating a mid-saga
// fault that must trigger compensation.
type failingStore struct {
	*tenant.Store
	failKey bool
}

func (f *failingStore) CreateAPIKey(ctx context.Context, tenantID, name, scope string) (tenant.APIKey, error) {
	if f.failKey {
		return tenant.APIKey{}, errors.New("injected key-mint failure")
	}
	return f.Store.CreateAPIKey(ctx, tenantID, name, scope)
}

func TestOnboardingCompensatesOnStepFailure(t *testing.T) {
	store := &failingStore{Store: tenant.NewStore(), failKey: true}
	states := saga.NewMemoryStateStore()
	ctx := t.Context()

	result, err := saga.Onboard(ctx, store, states, "sg-2", tenant.Tenant{ID: "acme", Name: "Acme"}, nil)
	if err == nil {
		t.Fatal("onboard should propagate the injected failure")
	}
	if result.State.Status != saga.StatusCompensated {
		t.Fatalf("status = %s, want compensated", result.State.Status)
	}

	// Compensation must leave no partial state: the tenant created by step
	// one is gone again.
	if _, ok, _ := store.Tenant(ctx, "acme"); ok {
		t.Fatal("compensation left the tenant behind")
	}

	// The stream records both the creation and its compensating deletion.
	pending, _ := store.Unpublished(ctx, 100)
	var sawCreated, sawDeleted bool
	for _, e := range pending {
		switch e.Action {
		case events.ActionTenantCreated:
			sawCreated = true
		case events.ActionTenantDeleted:
			sawDeleted = true
		}
	}
	if !sawCreated || !sawDeleted {
		t.Fatalf("audit trail incomplete: created=%v deleted=%v", sawCreated, sawDeleted)
	}
}

func TestOnboardingResumesAfterCrash(t *testing.T) {
	store := tenant.NewStore()
	states := saga.NewMemoryStateStore()
	ctx := t.Context()

	// Simulate a run that crashed after completing the first two steps: the
	// domain writes exist and the persisted cursor says StepsDone=2, but the
	// event-only steps never ran.
	if _, err := store.CreateTenant(ctx, tenant.NormalizeTenant(tenant.Tenant{ID: "acme", Name: "Acme"})); err != nil {
		t.Fatalf("seed tenant: %v", err)
	}
	if _, err := store.CreateAPIKey(ctx, "acme", "bootstrap", tenant.ScopeFull); err != nil {
		t.Fatalf("seed key: %v", err)
	}
	if err := states.SaveSagaState(ctx, saga.State{
		ID: "sg-3", Name: "tenant-onboarding", TenantID: "acme",
		Status: saga.StatusRunning, StepsDone: 2,
	}); err != nil {
		t.Fatalf("seed saga state: %v", err)
	}

	result, err := saga.Onboard(ctx, store, states, "sg-3", tenant.Tenant{ID: "acme", Name: "Acme"}, nil)
	if err != nil {
		t.Fatalf("resume onboard: %v", err)
	}
	if result.State.Status != saga.StatusCompleted {
		t.Fatalf("status = %s, want completed", result.State.Status)
	}

	// Resume must not double anything: still exactly one key, and the
	// deterministic event IDs appear exactly once.
	keys, _ := store.ListAPIKeys(ctx, "acme")
	if len(keys) != 1 {
		t.Fatalf("resume minted extra keys: %d", len(keys))
	}
	pending, _ := store.Unpublished(ctx, 100)
	counts := map[string]int{}
	for _, e := range pending {
		counts[e.ID]++
	}
	for id, n := range counts {
		if n != 1 {
			t.Fatalf("event %s appears %d times", id, n)
		}
	}
	if counts["saga_sg-3_policy"] != 1 || counts["saga_sg-3_welcome"] != 1 {
		t.Fatalf("event-only steps did not run on resume: %v", counts)
	}
}

func TestFinishedSagaIsSticky(t *testing.T) {
	store := tenant.NewStore()
	states := saga.NewMemoryStateStore()
	ctx := t.Context()

	if _, err := saga.Onboard(ctx, store, states, "sg-4", tenant.Tenant{ID: "acme", Name: "Acme"}, nil); err != nil {
		t.Fatalf("first onboard: %v", err)
	}
	result, err := saga.Onboard(ctx, store, states, "sg-4", tenant.Tenant{ID: "acme", Name: "Acme"}, nil)
	if err != nil {
		t.Fatalf("second onboard: %v", err)
	}
	if result.State.Status != saga.StatusCompleted {
		t.Fatalf("status = %s, want completed", result.State.Status)
	}
	keys, _ := store.ListAPIKeys(ctx, "acme")
	if len(keys) != 1 {
		t.Fatalf("re-running a completed saga minted extra keys: %d", len(keys))
	}
}
