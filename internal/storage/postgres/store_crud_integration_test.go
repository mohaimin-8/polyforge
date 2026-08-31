package postgres

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"polyforge/internal/events"
	"polyforge/internal/saga"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// PostgreSQL is the production storage backend, and before these tests its
// package measured 32.7% with only the row-level-security path covered: the
// tenant/project/API-key CRUD, the outbox, and the saga state store had no
// tests at all. The SQLite store — which is the one NOT used in production —
// sat at 97.3%. These close that inversion.
//
// They share `openIntegrationStore` with the RLS suite, so they skip
// identically when POLYFORGE_TEST_POSTGRES_* is unset. A skip prints `ok`, so
// never read a green run here as evidence the database paths were exercised;
// bring the fixture up with `./scripts/pg-test-up.sh` first.

// uniqueID keeps parallel runs and reruns from colliding on primary keys
// without needing a truncate between tests.
func uniqueID(t *testing.T, prefix string) string {
	t.Helper()
	return fmt.Sprintf("%s-%d", prefix, time.Now().UnixNano())
}

func newTenant(t *testing.T, ctx context.Context, store *Store, prefix string) tenant.Tenant {
	t.Helper()
	id := uniqueID(t, prefix)
	created, err := store.CreateTenant(ctx, tenant.Tenant{ID: id, Name: "T " + id})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = store.admin.Exec(context.Background(), `DELETE FROM tenants WHERE id = $1`, id)
	})
	return created
}

func TestProvisionTenantReturnsATenantAndItsFirstKey(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	id := uniqueID(t, "prov")
	t.Cleanup(func() {
		_, _ = store.admin.Exec(context.Background(), `DELETE FROM tenants WHERE id = $1`, id)
	})

	created, key, err := store.ProvisionTenant(ctx, tenant.Tenant{ID: id, Name: "Provisioned"}, "bootstrap")
	if err != nil {
		t.Fatal(err)
	}
	if created.ID != id {
		t.Errorf("tenant ID = %q, want %q", created.ID, id)
	}
	if key.TenantID != id {
		t.Errorf("key tenant = %q, want %q", key.TenantID, id)
	}
	if key.Name != "bootstrap" {
		t.Errorf("key name = %q, want %q", key.Name, "bootstrap")
	}
	// Provisioning is the only path that hands back a usable secret; if it
	// stopped doing so the caller would have a tenant it cannot authenticate
	// against, and every other test here would still pass.
	if key.Secret == "" {
		t.Error("ProvisionTenant returned an empty secret; the tenant would be unreachable")
	}

	got, ok, err := store.Tenant(ctx, id)
	if err != nil || !ok {
		t.Fatalf("Tenant(%q) = ok %v, err %v", id, ok, err)
	}
	if got.Name != "Provisioned" {
		t.Errorf("round-tripped name = %q, want %q", got.Name, "Provisioned")
	}
}

func TestTenantReportsMissingRatherThanErroring(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	_, ok, err := store.Tenant(ctx, uniqueID(t, "absent"))
	if err != nil {
		t.Fatalf("a missing tenant must not be an error: %v", err)
	}
	if ok {
		t.Error("Tenant reported a tenant that was never created")
	}
}

func TestListTenantsIncludesWhatWasCreated(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	created := newTenant(t, ctx, store, "list")
	all, err := store.ListTenants(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, candidate := range all {
		if candidate.ID == created.ID {
			return
		}
	}
	t.Errorf("ListTenants did not include %q (%d tenants returned)", created.ID, len(all))
}

func TestPromoteTenantIsolationMovesUpTheLadderOnly(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	created := newTenant(t, ctx, store, "iso")
	promoted, err := store.PromoteTenantIsolation(ctx, created.ID, tenant.IsolationBridge)
	if err != nil {
		t.Fatal(err)
	}
	if promoted.IsolationMode != tenant.IsolationBridge {
		t.Errorf("isolation = %q, want %q", promoted.IsolationMode, tenant.IsolationBridge)
	}

	// Downward is not a promotion. Without this the method would happily
	// demote a silo tenant back to pool, which is a data-exposure change
	// dressed as a config edit.
	if _, err := store.PromoteTenantIsolation(ctx, created.ID, tenant.IsolationPool); err == nil {
		t.Error("bridge -> pool was accepted; promotion must be upward only")
	}
}

func TestProjectCRUDRoundTrips(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	owner := newTenant(t, ctx, store, "proj")
	projectID := uniqueID(t, "p")
	created, err := store.CreateProject(ctx, tenant.Project{
		ID: projectID, TenantID: owner.ID, Name: "First",
	})
	if err != nil {
		t.Fatal(err)
	}

	got, err := store.Project(ctx, owner.ID, created.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != "First" {
		t.Errorf("Project name = %q, want %q", got.Name, "First")
	}

	got.Name = "Renamed"
	updated, err := store.UpdateProject(ctx, got)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Name != "Renamed" {
		t.Errorf("UpdateProject name = %q, want %q", updated.Name, "Renamed")
	}

	page, err := store.ListProjects(ctx, owner.ID, tenant.PageRequest{Limit: 10})
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, p := range page.Items {
		if p.ID == created.ID {
			found = true
			if p.Name != "Renamed" {
				t.Errorf("listed name = %q, want the updated %q", p.Name, "Renamed")
			}
		}
	}
	if !found {
		t.Errorf("ListProjects did not return %q", created.ID)
	}

	if err := store.DeleteProject(ctx, owner.ID, created.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Project(ctx, owner.ID, created.ID); err == nil {
		t.Error("Project still resolved after DeleteProject")
	}
}

func TestAPIKeyLifecycle(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	owner := newTenant(t, ctx, store, "key")
	created, err := store.CreateAPIKey(ctx, owner.ID, "ci", "full")
	if err != nil {
		t.Fatal(err)
	}

	keys, err := store.ListAPIKeys(ctx, owner.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(keys) == 0 {
		t.Fatal("ListAPIKeys returned nothing for a tenant that has one")
	}
	// The list must never carry the secret: it is shown once, at creation.
	for _, k := range keys {
		if k.Secret != "" {
			t.Errorf("ListAPIKeys leaked a secret for key %q", k.ID)
		}
	}

	if err := store.RevokeAPIKey(ctx, owner.ID, created.ID); err != nil {
		t.Fatal(err)
	}
	// Authentication after revocation is the property that matters; a revoked
	// key still appearing in a listing is cosmetic, one that still
	// authenticates is a breach.
	if _, err := store.AuthenticateAPIKey(ctx, owner.ID, created.Secret); err == nil {
		t.Error("a revoked key still authenticated")
	}
}

func TestOutboxAppendListAndMarkPublished(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	owner := newTenant(t, ctx, store, "outbox")
	id := uniqueID(t, "evt")
	event := events.Event{
		ID:         id,
		TenantID:   owner.ID,
		Action:     "tenant.created",
		Subject:    "tenant." + owner.ID,
		OccurredAt: time.Now().UTC().Truncate(time.Millisecond),
		Payload:    json.RawMessage(`{"k":"v"}`),
	}
	if err := store.AppendEvent(ctx, event); err != nil {
		t.Fatal(err)
	}

	pending, err := store.Unpublished(ctx, 100)
	if err != nil {
		t.Fatal(err)
	}
	if !containsEvent(pending, id) {
		t.Fatalf("appended event %q was not returned as unpublished", id)
	}

	if err := store.MarkPublished(ctx, []string{id}, time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	after, err := store.Unpublished(ctx, 100)
	if err != nil {
		t.Fatal(err)
	}
	// Without this the relay would republish every event forever, which looks
	// exactly like a healthy relay until a consumer counts duplicates.
	if containsEvent(after, id) {
		t.Errorf("event %q is still unpublished after MarkPublished", id)
	}
}

func containsEvent(evts []events.Event, id string) bool {
	for _, e := range evts {
		if e.ID == id {
			return true
		}
	}
	return false
}

func TestSagaStateRoundTrips(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	owner := newTenant(t, ctx, store, "saga")
	id := uniqueID(t, "s")
	state := saga.State{
		ID:        id,
		Name:      "provision",
		TenantID:  owner.ID,
		Status:    saga.StatusRunning,
		StepsDone: 2,
		UpdatedAt: time.Now().UTC().Truncate(time.Millisecond),
	}
	if err := store.SaveSagaState(ctx, state); err != nil {
		t.Fatal(err)
	}

	loaded, ok, err := store.LoadSagaState(ctx, id)
	if err != nil || !ok {
		t.Fatalf("LoadSagaState = ok %v, err %v", ok, err)
	}
	if loaded.Status != saga.StatusRunning || loaded.StepsDone != 2 {
		t.Errorf("loaded = {%v, %d}, want {%v, 2}", loaded.Status, loaded.StepsDone, saga.StatusRunning)
	}

	// Save is an upsert: a saga that advanced must not become a second row,
	// or recovery would replay from the older one.
	state.Status = saga.StatusCompleted
	state.StepsDone = 5
	if err := store.SaveSagaState(ctx, state); err != nil {
		t.Fatal(err)
	}
	loaded, _, err = store.LoadSagaState(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Status != saga.StatusCompleted || loaded.StepsDone != 5 {
		t.Errorf("after upsert = {%v, %d}, want {%v, 5}", loaded.Status, loaded.StepsDone, saga.StatusCompleted)
	}

	if _, ok, err := store.LoadSagaState(ctx, uniqueID(t, "missing")); err != nil || ok {
		t.Errorf("missing saga = ok %v, err %v; want ok=false, err=nil", ok, err)
	}
}

// The contract is "the N most recent events, returned in CHRONOLOGICAL order":
// the query takes `ORDER BY timestamp DESC LIMIT n` and then reverses the
// slice, and internal/storage/sqlite does the identical thing. Asserting both
// halves matters, because each failure mode is silent on its own — dropping
// the reversal returns the right events backwards, and flipping the ORDER BY
// returns the OLDEST n in the right order.
func TestRecentByTenantReturnsTheNewestEventsInChronologicalOrder(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	owner := newTenant(t, ctx, store, "recent")
	base := time.Now().UTC().Truncate(time.Second)
	for i, at := range []time.Time{base.Add(-3 * time.Minute), base.Add(-2 * time.Minute), base.Add(-time.Minute)} {
		if _, err := store.Add(ctx, telemetry.Event{
			TenantID: owner.ID, Service: fmt.Sprintf("svc-%d", i),
			Timestamp: at, LatencyMS: float64(10 * (i + 1)),
		}); err != nil {
			t.Fatal(err)
		}
	}

	recent, err := store.RecentByTenant(ctx, owner.ID, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(recent) != 2 {
		t.Fatalf("RecentByTenant(2) returned %d events, want 2", len(recent))
	}
	if recent[0].Timestamp.After(recent[1].Timestamp) {
		t.Errorf("not chronological: %v came before %v in the slice",
			recent[0].Timestamp, recent[1].Timestamp)
	}
	// The oldest of the three must have been dropped by the LIMIT. Without
	// this the test would pass on a query that returned the OLDEST two in
	// chronological order.
	oldest := base.Add(-3 * time.Minute)
	for _, e := range recent {
		if e.Timestamp.Equal(oldest) {
			t.Errorf("RecentByTenant(2) returned the oldest event %v; it should have "+
				"selected the two most recent", oldest)
		}
	}
}

func TestDeleteTenantRemovesIt(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	owner := newTenant(t, ctx, store, "del")
	if err := store.DeleteTenant(ctx, owner.ID); err != nil {
		t.Fatal(err)
	}
	if _, ok, err := store.Tenant(ctx, owner.ID); err != nil || ok {
		t.Errorf("tenant still present after DeleteTenant (ok %v, err %v)", ok, err)
	}
}
