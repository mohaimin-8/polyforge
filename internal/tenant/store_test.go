package tenant

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
)

func TestProvisionTenantIssuesBootstrapKeyOnce(t *testing.T) {
	ctx := context.Background()
	store := NewStore()

	created, key, err := store.ProvisionTenant(ctx, Tenant{ID: "alpha", Name: "Alpha"}, "bootstrap")
	if err != nil {
		t.Fatal(err)
	}
	if created.Plan != "standard" || created.IsolationMode != "pool" || created.CreatedAt.IsZero() {
		t.Fatalf("tenant defaults not applied: %+v", created)
	}
	if key.Secret == "" {
		t.Fatal("bootstrap key must return the secret exactly once")
	}

	listed, err := store.ListAPIKeys(ctx, "alpha")
	if err != nil {
		t.Fatal(err)
	}
	if len(listed) != 1 || listed[0].Secret != "" {
		t.Fatalf("stored key must never retain the secret: %+v", listed)
	}

	if _, _, err := store.ProvisionTenant(ctx, Tenant{ID: "alpha", Name: "Again"}, "boot"); !errors.Is(err, ErrConflict) {
		t.Fatalf("expected ErrConflict on duplicate provision, got %v", err)
	}
}

func TestCreateTenantNormalizesAndRejectsDuplicates(t *testing.T) {
	ctx := context.Background()
	store := NewStore()

	created, err := store.CreateTenant(ctx, Tenant{ID: "  alpha  ", Name: "  Alpha  "})
	if err != nil {
		t.Fatal(err)
	}
	if created.ID != "alpha" || created.Name != "Alpha" {
		t.Fatalf("expected trimmed identity, got %+v", created)
	}

	if _, err := store.CreateTenant(ctx, Tenant{ID: "alpha", Name: "Alpha"}); !errors.Is(err, ErrConflict) {
		t.Fatalf("expected ErrConflict, got %v", err)
	}

	got, ok, err := store.Tenant(ctx, "alpha")
	if err != nil || !ok || got.ID != "alpha" {
		t.Fatalf("lookup failed: %+v %v %v", got, ok, err)
	}
	if _, ok, _ := store.Tenant(ctx, "ghost"); ok {
		t.Fatal("unknown tenant must not resolve")
	}
}

func TestListTenantsSortedByID(t *testing.T) {
	ctx := context.Background()
	store := NewStore()
	for _, id := range []string{"zeta", "alpha", "mike"} {
		if _, err := store.CreateTenant(ctx, Tenant{ID: id, Name: id}); err != nil {
			t.Fatal(err)
		}
	}
	tenants, err := store.ListTenants(ctx)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"alpha", "mike", "zeta"}
	for i, tenant := range tenants {
		if tenant.ID != want[i] {
			t.Fatalf("expected sorted order %v, got %+v", want, tenants)
		}
	}
}

func TestProjectLifecycle(t *testing.T) {
	ctx := context.Background()
	store := NewStore()
	if _, err := store.CreateProject(ctx, Project{ID: "p1", TenantID: "ghost", Name: "X"}); !errors.Is(err, ErrTenantNotFound) {
		t.Fatalf("expected ErrTenantNotFound, got %v", err)
	}

	_, _ = store.CreateTenant(ctx, Tenant{ID: "alpha", Name: "Alpha"})
	if _, err := store.CreateProject(ctx, Project{ID: "p1", TenantID: "alpha", Name: "One"}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.CreateProject(ctx, Project{ID: "p1", TenantID: "alpha", Name: "Dup"}); !errors.Is(err, ErrConflict) {
		t.Fatalf("expected ErrConflict, got %v", err)
	}

	updated, err := store.UpdateProject(ctx, Project{ID: "p1", TenantID: "alpha", Name: "  Renamed  "})
	if err != nil {
		t.Fatal(err)
	}
	if updated.Name != "Renamed" {
		t.Fatalf("expected trimmed rename, got %q", updated.Name)
	}
	if _, err := store.UpdateProject(ctx, Project{ID: "nope", TenantID: "alpha", Name: "X"}); !errors.Is(err, ErrProjectNotFound) {
		t.Fatalf("expected ErrProjectNotFound, got %v", err)
	}

	if _, err := store.Project(ctx, "alpha", "p1"); err != nil {
		t.Fatal(err)
	}
	// A project must not be addressable through another tenant's scope.
	if _, err := store.Project(ctx, "bravo", "p1"); !errors.Is(err, ErrProjectNotFound) {
		t.Fatalf("cross-tenant read must fail with ErrProjectNotFound, got %v", err)
	}

	if err := store.DeleteProject(ctx, "alpha", "p1"); err != nil {
		t.Fatal(err)
	}
	if err := store.DeleteProject(ctx, "alpha", "p1"); !errors.Is(err, ErrProjectNotFound) {
		t.Fatalf("expected ErrProjectNotFound on double delete, got %v", err)
	}
}

func TestListProjectsKeysetPagination(t *testing.T) {
	ctx := context.Background()
	store := NewStore()
	_, _ = store.CreateTenant(ctx, Tenant{ID: "alpha", Name: "Alpha"})
	for i := 0; i < 5; i++ {
		id := fmt.Sprintf("p%d", i)
		if _, err := store.CreateProject(ctx, Project{ID: id, TenantID: "alpha", Name: id}); err != nil {
			t.Fatal(err)
		}
	}

	if _, err := store.ListProjects(ctx, "ghost", PageRequest{}); !errors.Is(err, ErrTenantNotFound) {
		t.Fatalf("expected ErrTenantNotFound, got %v", err)
	}

	first, err := store.ListProjects(ctx, "alpha", PageRequest{Limit: 2})
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Items) != 2 || first.NextCursor != "p1" {
		t.Fatalf("unexpected first page: %+v", first)
	}

	second, err := store.ListProjects(ctx, "alpha", PageRequest{Limit: 2, Cursor: first.NextCursor})
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Items) != 2 || second.Items[0].ID != "p2" || second.NextCursor != "p3" {
		t.Fatalf("unexpected second page: %+v", second)
	}

	last, err := store.ListProjects(ctx, "alpha", PageRequest{Limit: 2, Cursor: second.NextCursor})
	if err != nil {
		t.Fatal(err)
	}
	if len(last.Items) != 1 || last.NextCursor != "" {
		t.Fatalf("last page must not advertise a cursor: %+v", last)
	}

	clamped := NormalizePageRequest(PageRequest{Limit: MaxPageSize + 1000})
	if clamped.Limit != MaxPageSize {
		t.Fatalf("expected limit clamped to %d, got %d", MaxPageSize, clamped.Limit)
	}
	defaulted := NormalizePageRequest(PageRequest{})
	if defaulted.Limit != DefaultPageSize {
		t.Fatalf("expected default limit %d, got %d", DefaultPageSize, defaulted.Limit)
	}
}

func TestAPIKeyAuthenticationBoundaries(t *testing.T) {
	ctx := context.Background()
	store := NewStore()
	_, _ = store.CreateTenant(ctx, Tenant{ID: "alpha", Name: "Alpha"})
	_, _ = store.CreateTenant(ctx, Tenant{ID: "bravo", Name: "Bravo"})

	if _, err := store.CreateAPIKey(ctx, "ghost", "x"); !errors.Is(err, ErrTenantNotFound) {
		t.Fatalf("expected ErrTenantNotFound, got %v", err)
	}

	key, err := store.CreateAPIKey(ctx, "alpha", "primary")
	if err != nil {
		t.Fatal(err)
	}

	if _, err := store.AuthenticateAPIKey(ctx, "alpha", key.Secret); err != nil {
		t.Fatalf("valid key rejected: %v", err)
	}
	if _, err := store.AuthenticateAPIKey(ctx, "bravo", key.Secret); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("cross-tenant secret must fail with ErrUnauthorized, got %v", err)
	}
	if _, err := store.AuthenticateAPIKey(ctx, "alpha", "pf_live_totally-wrong"); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("garbage secret must fail with ErrUnauthorized, got %v", err)
	}

	if err := store.RevokeAPIKey(ctx, "alpha", key.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := store.AuthenticateAPIKey(ctx, "alpha", key.Secret); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("revoked key must fail with ErrUnauthorized, got %v", err)
	}
	// Revocation is idempotent.
	if err := store.RevokeAPIKey(ctx, "alpha", key.ID); err != nil {
		t.Fatalf("second revoke must be a no-op, got %v", err)
	}
	if err := store.RevokeAPIKey(ctx, "bravo", key.ID); !errors.Is(err, ErrAPIKeyNotFound) {
		t.Fatalf("cross-tenant revoke must fail with ErrAPIKeyNotFound, got %v", err)
	}
}

func TestRotateAPIKeyInvalidatesOldSecretAtomically(t *testing.T) {
	ctx := context.Background()
	store := NewStore()
	_, _ = store.CreateTenant(ctx, Tenant{ID: "alpha", Name: "Alpha"})
	old, err := store.CreateAPIKey(ctx, "alpha", "primary")
	if err != nil {
		t.Fatal(err)
	}

	rotated, err := store.RotateAPIKey(ctx, "alpha", old.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	if rotated.Name != "primary" {
		t.Fatalf("blank rotation name must inherit the old name, got %q", rotated.Name)
	}
	if _, err := store.AuthenticateAPIKey(ctx, "alpha", rotated.Secret); err != nil {
		t.Fatalf("rotated key rejected: %v", err)
	}
	if _, err := store.AuthenticateAPIKey(ctx, "alpha", old.Secret); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("old secret must be dead after rotation, got %v", err)
	}
	// A revoked key cannot be rotated again.
	if _, err := store.RotateAPIKey(ctx, "alpha", old.ID, "again"); !errors.Is(err, ErrAPIKeyNotFound) {
		t.Fatalf("rotating a revoked key must fail with ErrAPIKeyNotFound, got %v", err)
	}
}

func TestNewRandomAPIKeyShape(t *testing.T) {
	key, hash, err := NewRandomAPIKey("alpha", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(key.Secret, "pf_live_") || len(key.Secret) != len("pf_live_")+48 {
		t.Fatalf("unexpected secret shape: %q", key.Secret)
	}
	if key.Prefix != key.Secret[:12] {
		t.Fatalf("prefix must be the first 12 characters of the secret, got %q", key.Prefix)
	}
	if key.Name != "default" {
		t.Fatalf("blank name must default, got %q", key.Name)
	}
	if hash != HashAPIKey(key.Secret) {
		t.Fatal("returned hash must match the secret hash")
	}

	// The ID must not reuse the secret's bytes: IDs appear in URLs, listings,
	// and logs, and must not leak secret entropy.
	idHex := strings.TrimPrefix(key.ID, "key_")
	secretHex := strings.TrimPrefix(key.Secret, "pf_live_")
	if strings.HasPrefix(secretHex, idHex) || strings.Contains(secretHex, idHex) {
		t.Fatalf("key ID %q is derived from the secret %q", key.ID, key.Prefix)
	}

	other, _, err := NewRandomAPIKey("alpha", "x")
	if err != nil {
		t.Fatal(err)
	}
	if other.ID == key.ID || other.Secret == key.Secret {
		t.Fatal("two generated keys must not collide")
	}
}

// TestStoreConcurrentAccessIsRaceFree gives the race detector real
// contention across every mutex-guarded path. It only fails meaningfully
// under `go test -race`, which CI runs.
func TestStoreConcurrentAccessIsRaceFree(t *testing.T) {
	ctx := context.Background()
	store := NewStore()
	_, _ = store.CreateTenant(ctx, Tenant{ID: "alpha", Name: "Alpha"})
	seed, err := store.CreateAPIKey(ctx, "alpha", "seed")
	if err != nil {
		t.Fatal(err)
	}

	var wg sync.WaitGroup
	for worker := 0; worker < 8; worker++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			for i := 0; i < 25; i++ {
				id := fmt.Sprintf("w%d-p%d", worker, i)
				_, _ = store.CreateProject(ctx, Project{ID: id, TenantID: "alpha", Name: id})
				_, _ = store.ListProjects(ctx, "alpha", PageRequest{Limit: 10})
				_, _ = store.AuthenticateAPIKey(ctx, "alpha", seed.Secret)
				key, err := store.CreateAPIKey(ctx, "alpha", id)
				if err == nil && i%5 == 0 {
					_, _ = store.RotateAPIKey(ctx, "alpha", key.ID, "")
				}
				_, _ = store.ListAPIKeys(ctx, "alpha")
				_, _ = store.ListTenants(ctx)
			}
		}(worker)
	}
	wg.Wait()

	if _, err := store.AuthenticateAPIKey(ctx, "alpha", seed.Secret); err != nil {
		t.Fatalf("seed key must survive concurrent traffic: %v", err)
	}
}
