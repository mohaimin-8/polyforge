package sqlite

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"polyforge/internal/events"
	"polyforge/internal/tenant"
)

func TestPromoteTenantIsolationPersistsAndWritesOutbox(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "polyforge.db")
	store, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"}); err != nil {
		t.Fatal(err)
	}

	promoted, err := store.PromoteTenantIsolation(ctx, "alpha", tenant.IsolationSilo)
	if err != nil {
		t.Fatalf("promote: %v", err)
	}
	if promoted.IsolationMode != tenant.IsolationSilo {
		t.Fatalf("returned mode = %q, want silo", promoted.IsolationMode)
	}

	// The update must survive a reopen — it is a row, not cache state.
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	stored, ok, err := store.Tenant(ctx, "alpha")
	if err != nil || !ok {
		t.Fatalf("lookup after reopen: ok=%v err=%v", ok, err)
	}
	if stored.IsolationMode != tenant.IsolationSilo {
		t.Fatalf("persisted mode = %q, want silo", stored.IsolationMode)
	}

	unpublished, err := store.Unpublished(ctx, 100)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, e := range unpublished {
		if e.Action == events.ActionTenantPromoted && e.TenantID == "alpha" {
			found = true
		}
	}
	if !found {
		t.Fatal("promotion event missing from the outbox")
	}

	// Downgrades are rejected and change nothing.
	if _, err := store.PromoteTenantIsolation(ctx, "alpha", tenant.IsolationPool); !errors.Is(err, tenant.ErrInvalidPromotion) {
		t.Fatalf("downgrade err = %v, want ErrInvalidPromotion", err)
	}
	if _, err := store.PromoteTenantIsolation(ctx, "ghost", tenant.IsolationSilo); !errors.Is(err, tenant.ErrTenantNotFound) {
		t.Fatalf("unknown tenant err = %v, want ErrTenantNotFound", err)
	}
}
