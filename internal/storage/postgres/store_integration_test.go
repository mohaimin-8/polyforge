package postgres

import (
	"context"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// openIntegrationStore skips the test unless the PostgreSQL fixture URLs are
// configured, then returns a migrated store bound to the test lifetime.
func openIntegrationStore(t *testing.T, ctx context.Context) *Store {
	t.Helper()
	adminURL := os.Getenv("POLYFORGE_TEST_POSTGRES_ADMIN_URL")
	appURL := os.Getenv("POLYFORGE_TEST_POSTGRES_APP_URL")
	if adminURL == "" || appURL == "" {
		t.Skip("PostgreSQL integration URLs are not configured")
	}
	store, err := Open(ctx, Config{AdminURL: adminURL, AppURL: appURL})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	return store
}

func TestPostgresFeaturesAreTenantScopedByRowLevelSecurity(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	suffix := fmt.Sprintf("%d", time.Now().UnixNano())
	alpha := "feat-alpha-" + suffix
	bravo := "feat-bravo-" + suffix
	t.Cleanup(func() {
		_, _ = store.admin.Exec(context.Background(), `DELETE FROM tenants WHERE id IN ($1, $2)`, alpha, bravo)
	})
	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: alpha, Name: "Alpha"}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: bravo, Name: "Bravo"}); err != nil {
		t.Fatal(err)
	}

	base := time.Now().UTC().Truncate(time.Second)
	events := []telemetry.Event{
		{TenantID: alpha, Service: "api", Timestamp: base.Add(-10 * time.Minute), LatencyMS: 10, PayloadBytes: 100, CacheHit: true, ModelTier: "small"},
		{TenantID: alpha, Service: "api", Timestamp: base.Add(-9 * time.Minute), LatencyMS: 20, PayloadBytes: 200, CacheHit: false, ModelTier: "small"},
		{TenantID: alpha, Service: "worker", Timestamp: base.Add(-8 * time.Minute), LatencyMS: 100, PayloadBytes: 900, CacheHit: false, ModelTier: "mid"},
		{TenantID: alpha, Service: "api", Timestamp: base.Add(-90 * time.Minute), LatencyMS: 5000, PayloadBytes: 5000, CacheHit: true, ModelTier: "large"},
		{TenantID: bravo, Service: "api", Timestamp: base.Add(-8 * time.Minute), LatencyMS: 999, PayloadBytes: 999, CacheHit: true, ModelTier: "large"},
	}
	for _, event := range events {
		if _, err := store.Add(ctx, event); err != nil {
			t.Fatal(err)
		}
	}

	// The default 15-minute lookback must exclude the 90-minute-old outlier.
	set, err := store.Features(ctx, alpha, telemetry.FeatureQuery{})
	if err != nil {
		t.Fatal(err)
	}
	if len(set.Items) != 2 {
		t.Fatalf("expected api and worker rows, got %+v", set.Items)
	}
	api := set.Items[0]
	if api.Service != "api" || api.EventCount != 2 {
		t.Fatalf("expected 2 recent api events, got %+v", api)
	}
	if api.AvgLatencyMS != 15 || api.P95LatencyMS != 20 || api.CacheHitRate != 0.5 {
		t.Fatalf("unexpected api aggregation: %+v", api)
	}
	if api.ModelTierCounts["large"] != 0 {
		t.Fatalf("stale event leaked into window: %+v", api.ModelTierCounts)
	}

	filtered, err := store.Features(ctx, alpha, telemetry.FeatureQuery{Service: "worker"})
	if err != nil {
		t.Fatal(err)
	}
	if len(filtered.Items) != 1 || filtered.Items[0].Service != "worker" {
		t.Fatalf("service filter failed: %+v", filtered.Items)
	}

	if _, err := store.Features(ctx, "ghost-"+suffix, telemetry.FeatureQuery{}); !errors.Is(err, tenant.ErrTenantNotFound) {
		t.Fatalf("expected ErrTenantNotFound for unknown tenant, got %v", err)
	}

	// The feature query filters by tenant_id, so query telemetry_events without
	// that predicate to prove row-level security also guards the analytical path.
	visible, err := withTenantTx(ctx, store.app, bravo, func(tx pgx.Tx) (int, error) {
		var count int
		err := tx.QueryRow(ctx, `SELECT count(*) FROM telemetry_events`).Scan(&count)
		return count, err
	})
	if err != nil {
		t.Fatal(err)
	}
	if visible != 1 {
		t.Fatalf("bravo context observed %d telemetry events, expected only its own", visible)
	}
}

func TestPostgresRowLevelIsolation(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := openIntegrationStore(t, ctx)

	suffix := fmt.Sprintf("%d", time.Now().UnixNano())
	alpha := "test-alpha-" + suffix
	bravo := "test-bravo-" + suffix
	defer func() {
		_, _ = store.admin.Exec(context.Background(), `DELETE FROM tenants WHERE id IN ($1, $2)`, alpha, bravo)
	}()
	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: alpha, Name: "Alpha"}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: bravo, Name: "Bravo"}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.CreateProject(ctx, tenant.Project{ID: "private", TenantID: bravo, Name: "Bravo private"}); err != nil {
		t.Fatal(err)
	}

	visible, err := withTenantTx(ctx, store.app, alpha, func(tx pgx.Tx) (int, error) {
		var count int
		err := tx.QueryRow(ctx, `SELECT count(*) FROM projects WHERE tenant_id = $1`, bravo).Scan(&count)
		return count, err
	})
	if err != nil {
		t.Fatal(err)
	}
	if visible != 0 {
		t.Fatalf("alpha transaction observed %d bravo projects", visible)
	}

	_, err = withTenantTx(ctx, store.app, alpha, func(tx pgx.Tx) (struct{}, error) {
		_, err := tx.Exec(ctx, `
			INSERT INTO projects(id, tenant_id, name, created_at, updated_at)
			VALUES ('forbidden', $1, 'cross-tenant', now(), now())
		`, bravo)
		return struct{}{}, err
	})
	if err == nil {
		t.Fatal("RLS allowed alpha context to insert a bravo project")
	}

	var visibleWithoutContext int
	if err := store.app.QueryRow(ctx, `SELECT count(*) FROM tenants`).Scan(&visibleWithoutContext); err != nil {
		t.Fatal(err)
	}
	if visibleWithoutContext != 0 {
		t.Fatalf("app connection without tenant context observed %d tenants", visibleWithoutContext)
	}
}
