package tenant_test

import (
	"context"
	"errors"
	"testing"

	"polyforge/internal/events"
	"polyforge/internal/tenant"
)

func TestValidatePromotionIsUpwardOnly(t *testing.T) {
	cases := []struct {
		from, to string
		ok       bool
	}{
		{"pool", "bridge", true},
		{"pool", "silo", true}, // skipping a level is allowed
		{"bridge", "silo", true},
		{"", "bridge", true}, // empty normalizes to pool
		{"bridge", "pool", false},
		{"silo", "bridge", false},
		{"pool", "pool", false},
		{"pool", "fortress", false},
		{"fortress", "silo", false},
	}
	for _, tc := range cases {
		err := tenant.ValidatePromotion(tc.from, tc.to)
		if tc.ok && err != nil {
			t.Errorf("ValidatePromotion(%q, %q) = %v, want nil", tc.from, tc.to, err)
		}
		if !tc.ok {
			if err == nil {
				t.Errorf("ValidatePromotion(%q, %q) = nil, want error", tc.from, tc.to)
			} else if !errors.Is(err, tenant.ErrInvalidPromotion) {
				t.Errorf("ValidatePromotion(%q, %q) = %v, want ErrInvalidPromotion", tc.from, tc.to, err)
			}
		}
	}
}

func TestPromoteTenantIsolationUpdatesAndAudits(t *testing.T) {
	ctx := context.Background()
	store := tenant.NewStore()
	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}); err != nil {
		t.Fatalf("create: %v", err)
	}

	promoted, err := store.PromoteTenantIsolation(ctx, "acme", tenant.IsolationBridge)
	if err != nil {
		t.Fatalf("promote: %v", err)
	}
	if promoted.IsolationMode != tenant.IsolationBridge {
		t.Fatalf("mode = %q, want bridge", promoted.IsolationMode)
	}
	stored, ok, err := store.Tenant(ctx, "acme")
	if err != nil || !ok {
		t.Fatalf("lookup: ok=%v err=%v", ok, err)
	}
	if stored.IsolationMode != tenant.IsolationBridge {
		t.Fatalf("stored mode = %q, want bridge", stored.IsolationMode)
	}

	// The transition lands in the outbox so silo/bridge automation can react.
	unpublished, err := store.Unpublished(ctx, 100)
	if err != nil {
		t.Fatalf("unpublished: %v", err)
	}
	found := false
	for _, e := range unpublished {
		if e.Action == events.ActionTenantPromoted && e.TenantID == "acme" {
			found = true
		}
	}
	if !found {
		t.Fatal("no tenant.promoted event in the outbox")
	}
}

func TestPromoteTenantIsolationRejectsDowngradeAndUnknownTenant(t *testing.T) {
	ctx := context.Background()
	store := tenant.NewStore()
	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme", IsolationMode: tenant.IsolationSilo}); err != nil {
		t.Fatalf("create: %v", err)
	}

	if _, err := store.PromoteTenantIsolation(ctx, "acme", tenant.IsolationPool); !errors.Is(err, tenant.ErrInvalidPromotion) {
		t.Fatalf("downgrade err = %v, want ErrInvalidPromotion", err)
	}
	// The failed call must not have moved the tenant.
	stored, _, _ := store.Tenant(ctx, "acme")
	if stored.IsolationMode != tenant.IsolationSilo {
		t.Fatalf("mode after rejected downgrade = %q, want silo", stored.IsolationMode)
	}

	if _, err := store.PromoteTenantIsolation(ctx, "ghost", tenant.IsolationSilo); !errors.Is(err, tenant.ErrTenantNotFound) {
		t.Fatalf("unknown tenant err = %v, want ErrTenantNotFound", err)
	}
}
