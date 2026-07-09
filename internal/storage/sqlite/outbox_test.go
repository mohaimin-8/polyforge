package sqlite

import (
	"path/filepath"
	"testing"
	"time"

	"polyforge/internal/events"
	"polyforge/internal/saga"
	"polyforge/internal/tenant"
)

func openOutboxStore(t *testing.T) *Store {
	t.Helper()
	store, err := Open(t.Context(), filepath.Join(t.TempDir(), "outbox_test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return store
}

func TestWritesAppendToOutboxTransactionally(t *testing.T) {
	store := openOutboxStore(t)
	ctx := t.Context()

	if _, _, err := store.ProvisionTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}, "bootstrap"); err != nil {
		t.Fatalf("provision: %v", err)
	}
	project, err := store.CreateProject(ctx, tenant.Project{ID: "web", TenantID: "acme", Name: "Web"})
	if err != nil {
		t.Fatalf("create project: %v", err)
	}
	if _, err := store.UpdateProject(ctx, tenant.Project{ID: project.ID, TenantID: "acme", Name: "Web v2"}); err != nil {
		t.Fatalf("update project: %v", err)
	}
	if err := store.DeleteProject(ctx, "acme", "web"); err != nil {
		t.Fatalf("delete project: %v", err)
	}
	key, err := store.CreateAPIKey(ctx, "acme", "ci", tenant.ScopeRead)
	if err != nil {
		t.Fatalf("create key: %v", err)
	}
	rotated, err := store.RotateAPIKey(ctx, "acme", key.ID, "")
	if err != nil {
		t.Fatalf("rotate key: %v", err)
	}
	if err := store.RevokeAPIKey(ctx, "acme", rotated.ID); err != nil {
		t.Fatalf("revoke key: %v", err)
	}

	pending, err := store.Unpublished(ctx, 100)
	if err != nil {
		t.Fatalf("unpublished: %v", err)
	}
	wantOrder := []string{
		events.ActionTenantProvisioned,
		events.ActionProjectCreated,
		events.ActionProjectUpdated,
		events.ActionProjectDeleted,
		events.ActionAPIKeyCreated,
		events.ActionAPIKeyRotated,
		events.ActionAPIKeyRevoked,
	}
	if len(pending) != len(wantOrder) {
		t.Fatalf("outbox holds %d events, want %d", len(pending), len(wantOrder))
	}
	for i, action := range wantOrder {
		if pending[i].Action != action {
			t.Fatalf("outbox[%d] = %s, want %s (write order must be outbox order)", i, pending[i].Action, action)
		}
	}

	// Marking published removes events from the pending set and is final.
	ids := []string{pending[0].ID, pending[1].ID}
	if err := store.MarkPublished(ctx, ids, time.Now().UTC()); err != nil {
		t.Fatalf("mark published: %v", err)
	}
	remaining, _ := store.Unpublished(ctx, 100)
	if len(remaining) != len(wantOrder)-2 {
		t.Fatalf("after publish, %d events pending, want %d", len(remaining), len(wantOrder)-2)
	}
}

func TestAppendEventIsIdempotentByID(t *testing.T) {
	store := openOutboxStore(t)
	ctx := t.Context()

	event, err := events.NewWithID("saga_x_policy", "acme", events.ActionPolicyDefaulted, map[string]string{"tier": "small"})
	if err != nil {
		t.Fatalf("build event: %v", err)
	}
	if err := store.AppendEvent(ctx, event); err != nil {
		t.Fatalf("first append: %v", err)
	}
	if err := store.AppendEvent(ctx, event); err != nil {
		t.Fatalf("second append: %v", err)
	}
	pending, _ := store.Unpublished(ctx, 10)
	if len(pending) != 1 {
		t.Fatalf("deterministic-ID append duplicated: %d rows", len(pending))
	}
}

func TestDeleteTenantCascadesAndAudits(t *testing.T) {
	store := openOutboxStore(t)
	ctx := t.Context()

	if _, _, err := store.ProvisionTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}, "bootstrap"); err != nil {
		t.Fatalf("provision: %v", err)
	}
	if _, err := store.CreateProject(ctx, tenant.Project{ID: "web", TenantID: "acme", Name: "Web"}); err != nil {
		t.Fatalf("create project: %v", err)
	}
	if err := store.DeleteTenant(ctx, "acme"); err != nil {
		t.Fatalf("delete tenant: %v", err)
	}

	if _, ok, _ := store.Tenant(ctx, "acme"); ok {
		t.Fatal("tenant survived deletion")
	}
	if _, err := store.Project(ctx, "acme", "web"); err != tenant.ErrProjectNotFound {
		t.Fatalf("project survived tenant deletion: %v", err)
	}

	// The audit trail must outlive the tenant: no FK cascade on outbox.
	pending, _ := store.Unpublished(ctx, 100)
	var sawDeleted bool
	for _, e := range pending {
		if e.Action == events.ActionTenantDeleted {
			sawDeleted = true
		}
	}
	if !sawDeleted || len(pending) < 3 {
		t.Fatalf("audit trail truncated by tenant deletion: %d events, deleted=%v", len(pending), sawDeleted)
	}

	if err := store.DeleteTenant(ctx, "acme"); err != tenant.ErrTenantNotFound {
		t.Fatalf("second delete = %v, want ErrTenantNotFound", err)
	}
}

func TestSagaStateRoundTripAndOnboarding(t *testing.T) {
	store := openOutboxStore(t)
	ctx := t.Context()

	// The full onboarding saga runs against SQLite exactly as it does
	// against the memory store: same OnboardingStore surface.
	result, err := saga.Onboard(ctx, store, store, "sg-sqlite", tenant.Tenant{ID: "acme", Name: "Acme"}, nil)
	if err != nil {
		t.Fatalf("onboard: %v", err)
	}
	if result.State.Status != saga.StatusCompleted {
		t.Fatalf("status = %s, want completed", result.State.Status)
	}

	state, found, err := store.LoadSagaState(ctx, "sg-sqlite")
	if err != nil || !found {
		t.Fatalf("load saga state: found=%v err=%v", found, err)
	}
	if state.Status != saga.StatusCompleted || state.StepsDone != 4 {
		t.Fatalf("persisted state = %+v, want completed with 4 steps", state)
	}

	pending, _ := store.Unpublished(ctx, 100)
	ids := map[string]bool{}
	for _, e := range pending {
		ids[e.ID] = true
	}
	if !ids["saga_sg-sqlite_policy"] || !ids["saga_sg-sqlite_welcome"] {
		t.Fatalf("saga events missing from outbox: %v", ids)
	}
}
