package sqlite

import (
	"context"
	"database/sql"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

func TestStorePersistsTenantDataAndTelemetry(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "polyforge.db")

	store, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}

	createdTenant, err := store.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	if err != nil {
		t.Fatal(err)
	}
	if createdTenant.Plan != "standard" {
		t.Fatalf("expected default plan, got %q", createdTenant.Plan)
	}

	key, err := store.CreateAPIKey(ctx, "alpha", "integration", tenant.ScopeFull)
	if err != nil {
		t.Fatal(err)
	}
	if key.Secret == "" {
		t.Fatal("expected API key secret to be returned at creation")
	}

	if _, err := store.CreateProject(ctx, tenant.Project{ID: "p1", TenantID: "alpha", Name: "Research"}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Add(ctx, telemetry.Event{
		TenantID:         "alpha",
		Service:          "ai-gateway",
		RPSWindow:        25,
		PayloadBytes:     12000,
		LatencyMS:        450,
		CacheHit:         true,
		EmbeddingDensity: 0.73,
		ModelTier:        "mid",
	}); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = reopened.Close() }()

	if _, err := reopened.AuthenticateAPIKey(ctx, "alpha", key.Secret); err != nil {
		t.Fatalf("persisted API key did not authenticate: %v", err)
	}
	projects, err := reopened.ListProjects(ctx, "alpha", tenant.PageRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if len(projects.Items) != 1 || projects.Items[0].ID != "p1" {
		t.Fatalf("unexpected projects after reopen: %#v", projects)
	}
	events, err := reopened.RecentByTenant(ctx, "alpha", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 || events[0].Service != "ai-gateway" {
		t.Fatalf("unexpected telemetry after reopen: %#v", events)
	}
}

func TestProjectLifecycleAndPagination(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "polyforge.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()

	_, _ = store.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	for _, id := range []string{"p1", "p2", "p3"} {
		if _, err := store.CreateProject(ctx, tenant.Project{ID: id, TenantID: "alpha", Name: id}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := store.CreateProject(ctx, tenant.Project{ID: "p1", TenantID: "alpha", Name: "duplicate"}); !errors.Is(err, tenant.ErrConflict) {
		t.Fatalf("expected conflict, got %v", err)
	}

	first, err := store.ListProjects(ctx, "alpha", tenant.PageRequest{Limit: 2})
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Items) != 2 || first.NextCursor != "p2" {
		t.Fatalf("unexpected first page: %#v", first)
	}
	second, err := store.ListProjects(ctx, "alpha", tenant.PageRequest{Limit: 2, Cursor: first.NextCursor})
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Items) != 1 || second.Items[0].ID != "p3" || second.NextCursor != "" {
		t.Fatalf("unexpected second page: %#v", second)
	}

	before, err := store.Project(ctx, "alpha", "p2")
	if err != nil {
		t.Fatal(err)
	}
	updated, err := store.UpdateProject(ctx, tenant.Project{ID: "p2", TenantID: "alpha", Name: "Updated"})
	if err != nil {
		t.Fatal(err)
	}
	if updated.Name != "Updated" || updated.UpdatedAt.Before(before.UpdatedAt) {
		t.Fatalf("unexpected update: %#v", updated)
	}
	if err := store.DeleteProject(ctx, "alpha", "p2"); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Project(ctx, "alpha", "p2"); !errors.Is(err, tenant.ErrProjectNotFound) {
		t.Fatalf("expected project not found, got %v", err)
	}
}

func TestAPIKeyRotationAndRevocation(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "polyforge.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()

	_, _ = store.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	oldKey, err := store.CreateAPIKey(ctx, "alpha", "service", tenant.ScopeFull)
	if err != nil {
		t.Fatal(err)
	}
	newKey, err := store.RotateAPIKey(ctx, "alpha", oldKey.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	if newKey.Secret == "" || newKey.Name != oldKey.Name || newKey.Scope != tenant.ScopeFull {
		t.Fatalf("unexpected rotated key: %#v", newKey)
	}
	if _, err := store.AuthenticateAPIKey(ctx, "alpha", oldKey.Secret); !errors.Is(err, tenant.ErrUnauthorized) {
		t.Fatalf("expected old key rejection, got %v", err)
	}
	if _, err := store.AuthenticateAPIKey(ctx, "alpha", newKey.Secret); err != nil {
		t.Fatalf("new key did not authenticate: %v", err)
	}

	keys, err := store.ListAPIKeys(ctx, "alpha")
	if err != nil {
		t.Fatal(err)
	}
	if len(keys) != 2 || keys[0].Secret != "" || keys[0].RevokedAt == nil || keys[1].RevokedAt != nil {
		t.Fatalf("unexpected key metadata: %#v", keys)
	}
	if err := store.RevokeAPIKey(ctx, "alpha", newKey.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := store.AuthenticateAPIKey(ctx, "alpha", newKey.Secret); !errors.Is(err, tenant.ErrUnauthorized) {
		t.Fatalf("expected revoked key rejection, got %v", err)
	}

	// Read scope persists through the database and survives rotation.
	reader, err := store.CreateAPIKey(ctx, "alpha", "observer", tenant.ScopeRead)
	if err != nil {
		t.Fatal(err)
	}
	authenticated, err := store.AuthenticateAPIKey(ctx, "alpha", reader.Secret)
	if err != nil {
		t.Fatal(err)
	}
	if authenticated.Scope != tenant.ScopeRead {
		t.Fatalf("read scope did not persist, got %q", authenticated.Scope)
	}
	rotatedReader, err := store.RotateAPIKey(ctx, "alpha", reader.ID, "")
	if err != nil {
		t.Fatal(err)
	}
	if rotatedReader.Scope != tenant.ScopeRead {
		t.Fatalf("rotation must preserve scope, got %q", rotatedReader.Scope)
	}
}

func TestOpenMigratesLegacyProjectAndAPIKeyColumns(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "legacy.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	created := "2026-01-02T03:04:05Z"
	statements := []string{
		`CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL, isolation_mode TEXT NOT NULL, created_at TEXT NOT NULL)`,
		`CREATE TABLE projects (id TEXT NOT NULL, tenant_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (tenant_id, id))`,
		`CREATE TABLE api_keys (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)`,
		`INSERT INTO tenants VALUES ('alpha', 'Alpha', 'standard', 'pool', '` + created + `')`,
		`INSERT INTO projects VALUES ('p1', 'alpha', 'Legacy', '` + created + `')`,
		`INSERT INTO api_keys VALUES ('key_legacy', 'alpha', 'Legacy', 'pf_live_old', '` + tenant.HashAPIKey("legacy-secret") + `', '` + created + `')`,
	}
	for _, statement := range statements {
		if _, err := db.ExecContext(ctx, statement); err != nil {
			_ = db.Close()
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	store, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()
	project, err := store.Project(ctx, "alpha", "p1")
	if err != nil {
		t.Fatal(err)
	}
	if project.UpdatedAt.IsZero() || !project.UpdatedAt.Equal(project.CreatedAt) {
		t.Fatalf("legacy updated_at was not backfilled: %#v", project)
	}
	legacyKey, err := store.AuthenticateAPIKey(ctx, "alpha", "legacy-secret")
	if err != nil {
		t.Fatalf("legacy API key did not survive migration: %v", err)
	}
	// Keys issued before scopes existed keep their original full authority.
	if legacyKey.Scope != tenant.ScopeFull {
		t.Fatalf("legacy key scope was not backfilled to full, got %q", legacyKey.Scope)
	}
}

func TestProvisionTenantRollsBackWhenBootstrapKeyFails(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "polyforge.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()
	if _, err := store.db.ExecContext(ctx, `
		CREATE TRIGGER reject_bootstrap_key
		BEFORE INSERT ON api_keys
		BEGIN
			SELECT RAISE(ABORT, 'bootstrap key rejected');
		END
	`); err != nil {
		t.Fatal(err)
	}

	if _, _, err := store.ProvisionTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"}, "bootstrap"); err == nil {
		t.Fatal("expected bootstrap key insertion to fail")
	}
	if _, found, err := store.Tenant(ctx, "alpha"); err != nil {
		t.Fatal(err)
	} else if found {
		t.Fatal("tenant insert was not rolled back with bootstrap key failure")
	}
}

func TestStoreRejectsCrossTenantAPIKey(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "polyforge.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()

	_, _ = store.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	_, _ = store.CreateTenant(ctx, tenant.Tenant{ID: "bravo", Name: "Bravo"})
	key, err := store.CreateAPIKey(ctx, "alpha", "alpha-key", tenant.ScopeFull)
	if err != nil {
		t.Fatal(err)
	}

	if _, err := store.AuthenticateAPIKey(ctx, "bravo", key.Secret); err == nil {
		t.Fatal("expected alpha key to be rejected for bravo tenant")
	}
}

func TestFeaturesAggregatesWithinWindowAndTenant(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "polyforge.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()

	base := time.Now().UTC().Truncate(time.Second)
	_, _ = store.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	_, _ = store.CreateTenant(ctx, tenant.Tenant{ID: "bravo", Name: "Bravo"})

	events := []telemetry.Event{
		{TenantID: "alpha", Service: "api", Timestamp: base.Add(-10 * time.Minute), LatencyMS: 10, PayloadBytes: 100, CacheHit: true, ModelTier: "small"},
		{TenantID: "alpha", Service: "api", Timestamp: base.Add(-9 * time.Minute), LatencyMS: 20, PayloadBytes: 200, CacheHit: false, ModelTier: "small"},
		{TenantID: "alpha", Service: "worker", Timestamp: base.Add(-8 * time.Minute), LatencyMS: 100, PayloadBytes: 900, CacheHit: false, ModelTier: "mid"},
		{TenantID: "alpha", Service: "api", Timestamp: base.Add(-90 * time.Minute), LatencyMS: 5000, PayloadBytes: 5000, CacheHit: true, ModelTier: "large"},
		{TenantID: "bravo", Service: "api", Timestamp: base.Add(-8 * time.Minute), LatencyMS: 999, PayloadBytes: 999, CacheHit: true, ModelTier: "large"},
	}
	for _, event := range events {
		if _, err := store.Add(ctx, event); err != nil {
			t.Fatal(err)
		}
	}

	// The default 15-minute lookback must exclude the 90-minute-old outlier.
	set, err := store.Features(ctx, "alpha", telemetry.FeatureQuery{})
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
	if api.ModelTierCounts["small"] != 2 || api.ModelTierCounts["large"] != 0 {
		t.Fatalf("stale event leaked into window: %+v", api.ModelTierCounts)
	}

	filtered, err := store.Features(ctx, "alpha", telemetry.FeatureQuery{Service: "worker"})
	if err != nil {
		t.Fatal(err)
	}
	if len(filtered.Items) != 1 || filtered.Items[0].Service != "worker" {
		t.Fatalf("service filter failed: %+v", filtered.Items)
	}

	bounded, err := store.Features(ctx, "alpha", telemetry.FeatureQuery{Since: base.Add(-8*time.Minute - 30*time.Second)})
	if err != nil {
		t.Fatal(err)
	}
	if len(bounded.Items) != 1 || bounded.Items[0].Service != "worker" {
		t.Fatalf("since bound failed: %+v", bounded.Items)
	}

	other, err := store.Features(ctx, "bravo", telemetry.FeatureQuery{})
	if err != nil {
		t.Fatal(err)
	}
	if len(other.Items) != 1 || other.Items[0].EventCount != 1 {
		t.Fatalf("bravo observed alpha telemetry: %+v", other.Items)
	}

	if _, err := store.Features(ctx, "ghost", telemetry.FeatureQuery{}); !errors.Is(err, tenant.ErrTenantNotFound) {
		t.Fatalf("expected ErrTenantNotFound for unknown tenant, got %v", err)
	}
}

func TestMigrateNormalizesLegacyTimestampEncoding(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "polyforge.db")
	store, err := Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = store.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})

	// Rows as an older build wrote them: RFC3339Nano trims trailing zeros, so
	// whole seconds carry no fraction and these strings mis-sort against the
	// fixed-width form ("…00.5Z" < "…00Z" as TEXT).
	legacy := []struct {
		encoded string
		latency float64
	}{
		{"2026-01-01T12:00:00Z", 1},
		{"2026-01-01T12:00:00.5Z", 2},
		{"2026-01-01T12:00:01Z", 3}, // exactly at until: must stay excluded
	}
	for _, row := range legacy {
		if _, err := store.db.ExecContext(ctx, `
			INSERT INTO telemetry_events(
				tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms,
				cache_hit, embedding_density, model_tier, child_spans
			)
			VALUES ('alpha', 'api', ?, 0, 0, ?, 0, 0, 'small', 0)
		`, row.encoded, row.latency); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	// Reopening runs migrate, which pads legacy encodings in place.
	store, err = Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()

	since := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	set, err := store.Features(ctx, "alpha", telemetry.FeatureQuery{Since: since, Until: since.Add(time.Second)})
	if err != nil {
		t.Fatal(err)
	}
	if len(set.Items) != 1 || set.Items[0].EventCount != 2 {
		t.Fatalf("expected the [00.0s, 01.0s) window to admit exactly the 00.0 and 00.5 events, got %+v", set.Items)
	}
	if !set.Items[0].LastEventTimestamp.Equal(since.Add(500 * time.Millisecond)) {
		t.Fatalf("fractional legacy timestamp did not survive normalization: %+v", set.Items[0])
	}

	events, err := store.RecentByTenant(ctx, "alpha", 10)
	if err != nil {
		t.Fatal(err)
	}
	want := []time.Time{since, since.Add(500 * time.Millisecond), since.Add(time.Second)}
	if len(events) != len(want) {
		t.Fatalf("expected %d events, got %+v", len(want), events)
	}
	for i, event := range events {
		if !event.Timestamp.Equal(want[i]) {
			t.Fatalf("event %d: expected %v, got %v", i, want[i], event.Timestamp)
		}
	}
}

func TestFeaturesReportTruncationAtEventCap(t *testing.T) {
	ctx := context.Background()
	store, err := Open(ctx, filepath.Join(t.TempDir(), "polyforge.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = store.Close() }()

	_, _ = store.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	base := time.Now().UTC().Add(-5 * time.Minute).Truncate(time.Second)

	// MaxFeatureEvents+1 events, 1 ms apart, batched in one transaction so the
	// test stays fast under WAL.
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	stmt, err := tx.PrepareContext(ctx, `
		INSERT INTO telemetry_events(
			tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms,
			cache_hit, embedding_density, model_tier, child_spans
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i <= telemetry.MaxFeatureEvents; i++ {
		at := base.Add(time.Duration(i) * time.Millisecond)
		if _, err := stmt.ExecContext(ctx, "alpha", "api", encodeTime(at), 0.0, 0, float64(i), 0, 0.0, "small", 0); err != nil {
			t.Fatal(err)
		}
	}
	if err := stmt.Close(); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}

	set, err := store.Features(ctx, "alpha", telemetry.FeatureQuery{})
	if err != nil {
		t.Fatal(err)
	}
	if !set.Truncated {
		t.Fatal("expected Truncated=true when the window holds MaxFeatureEvents+1 events")
	}
	if len(set.Items) != 1 || set.Items[0].EventCount != telemetry.MaxFeatureEvents {
		t.Fatalf("expected exactly MaxFeatureEvents aggregated events, got %+v", set.Items)
	}
	// ORDER BY timestamp means the newest event is the one that gets cut.
	wantLast := base.Add(time.Duration(telemetry.MaxFeatureEvents-1) * time.Millisecond)
	if !set.Items[0].LastEventTimestamp.Equal(wantLast) {
		t.Fatalf("expected the earliest prefix to survive (last=%v), got last=%v", wantLast, set.Items[0].LastEventTimestamp)
	}

	bounded, err := store.Features(ctx, "alpha", telemetry.FeatureQuery{Since: base, Until: base.Add(time.Second)})
	if err != nil {
		t.Fatal(err)
	}
	if bounded.Truncated {
		t.Fatal("expected Truncated=false for a window under the cap")
	}
	if len(bounded.Items) != 1 || bounded.Items[0].EventCount != 1000 {
		t.Fatalf("expected the 1-second window to admit exactly 1000 events, got %+v", bounded.Items)
	}
}
