package sqlite

import (
	"context"
	"database/sql"
	"errors"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

type Store struct {
	db *sql.DB
}

func Open(ctx context.Context, path string) (*Store, error) {
	// WAL lets readers run concurrently with a writer and the busy timeout
	// absorbs writer contention between pooled connections; before this the
	// store was pinned to one connection and every request serialized on it
	// (measured: ~830 RPS ceiling on the projects list endpoint; see
	// artifacts/m4-projects-5krps.json for the after). The pragmas ride the
	// DSN so every pooled connection gets them, not only the first.
	dsn := "file:" + filepath.ToSlash(path) +
		"?_pragma=journal_mode(WAL)" +
		"&_pragma=busy_timeout(5000)" +
		"&_pragma=foreign_keys(1)" +
		"&_pragma=synchronous(NORMAL)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(max(4, runtime.NumCPU()))

	s := &Store{db: db}
	if err := s.migrate(ctx); err != nil {
		_ = db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func (s *Store) migrate(ctx context.Context) error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS tenants (
			id TEXT PRIMARY KEY,
			name TEXT NOT NULL,
			plan TEXT NOT NULL,
			isolation_mode TEXT NOT NULL,
			created_at TEXT NOT NULL
		);`,
		`CREATE TABLE IF NOT EXISTS projects (
			id TEXT NOT NULL,
			tenant_id TEXT NOT NULL,
			name TEXT NOT NULL,
			created_at TEXT NOT NULL,
			updated_at TEXT NOT NULL,
			PRIMARY KEY (tenant_id, id),
			FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
		);`,
		`CREATE TABLE IF NOT EXISTS api_keys (
			id TEXT PRIMARY KEY,
			tenant_id TEXT NOT NULL,
			name TEXT NOT NULL,
			prefix TEXT NOT NULL,
			key_hash TEXT NOT NULL UNIQUE,
			created_at TEXT NOT NULL,
			revoked_at TEXT,
			FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
		);`,
		`CREATE TABLE IF NOT EXISTS telemetry_events (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			tenant_id TEXT NOT NULL,
			service TEXT NOT NULL,
			timestamp TEXT NOT NULL,
			rps_window REAL NOT NULL,
			payload_bytes INTEGER NOT NULL,
			latency_ms REAL NOT NULL,
			cache_hit INTEGER NOT NULL,
			embedding_density REAL NOT NULL,
			model_tier TEXT NOT NULL,
			child_spans INTEGER NOT NULL,
			FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
		);`,
		`CREATE INDEX IF NOT EXISTS idx_telemetry_tenant_time ON telemetry_events(tenant_id, timestamp DESC);`,
		// Transactional outbox (ADR 0006). Deliberately no FK to tenants:
		// audit events must survive tenant deletion.
		`CREATE TABLE IF NOT EXISTS outbox (
			seq INTEGER PRIMARY KEY AUTOINCREMENT,
			id TEXT NOT NULL UNIQUE,
			tenant_id TEXT NOT NULL,
			action TEXT NOT NULL,
			subject TEXT NOT NULL,
			occurred_at TEXT NOT NULL,
			payload TEXT NOT NULL,
			published_at TEXT
		);`,
		`CREATE INDEX IF NOT EXISTS idx_outbox_unpublished ON outbox(seq) WHERE published_at IS NULL;`,
		// Saga state lives in the DB, not the broker (ADR 0006).
		`CREATE TABLE IF NOT EXISTS sagas (
			id TEXT PRIMARY KEY,
			name TEXT NOT NULL,
			tenant_id TEXT NOT NULL,
			status TEXT NOT NULL,
			steps_done INTEGER NOT NULL,
			error TEXT NOT NULL DEFAULT '',
			updated_at TEXT NOT NULL
		);`,
	}

	for _, stmt := range stmts {
		if _, err := s.db.ExecContext(ctx, stmt); err != nil {
			return err
		}
	}
	if err := s.ensureColumn(ctx, "projects", "updated_at", "TEXT"); err != nil {
		return err
	}
	if _, err := s.db.ExecContext(ctx, `UPDATE projects SET updated_at = created_at WHERE updated_at IS NULL OR updated_at = ''`); err != nil {
		return err
	}
	if err := s.ensureColumn(ctx, "api_keys", "revoked_at", "TEXT"); err != nil {
		return err
	}
	if err := s.ensureColumn(ctx, "api_keys", "scope", "TEXT"); err != nil {
		return err
	}
	// Keys issued before scopes existed keep their original full authority.
	if _, err := s.db.ExecContext(ctx, `UPDATE api_keys SET scope = 'full' WHERE scope IS NULL OR scope = ''`); err != nil {
		return err
	}
	return nil
}

func (s *Store) ensureColumn(ctx context.Context, table, column, definition string) error {
	rows, err := s.db.QueryContext(ctx, `PRAGMA table_info(`+table+`)`)
	if err != nil {
		return err
	}
	found := false
	for rows.Next() {
		var cid int
		var name, dataType string
		var notNull, primaryKey int
		var defaultValue any
		if err := rows.Scan(&cid, &name, &dataType, &notNull, &defaultValue, &primaryKey); err != nil {
			_ = rows.Close()
			return err
		}
		if name == column {
			found = true
			break
		}
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	if found {
		return nil
	}
	_, err = s.db.ExecContext(ctx, `ALTER TABLE `+table+` ADD COLUMN `+column+` `+definition)
	return err
}

func (s *Store) CreateTenant(ctx context.Context, t tenant.Tenant) (tenant.Tenant, error) {
	t = tenant.NormalizeTenant(t)
	event, err := tenant.AuditTenantCreated(t)
	if err != nil {
		return tenant.Tenant{}, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return tenant.Tenant{}, err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO tenants(id, name, plan, isolation_mode, created_at)
		VALUES (?, ?, ?, ?, ?)
	`, t.ID, t.Name, t.Plan, t.IsolationMode, encodeTime(t.CreatedAt)); err != nil {
		if sqliteConstraint(err) {
			return tenant.Tenant{}, tenant.ErrConflict
		}
		return tenant.Tenant{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.Tenant{}, err
	}
	return t, tx.Commit()
}

func (s *Store) ProvisionTenant(ctx context.Context, t tenant.Tenant, keyName string) (tenant.Tenant, tenant.APIKey, error) {
	t = tenant.NormalizeTenant(t)
	key, hash, err := tenant.NewRandomAPIKey(t.ID, keyName, tenant.ScopeFull)
	if err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO tenants(id, name, plan, isolation_mode, created_at)
		VALUES (?, ?, ?, ?, ?)
	`, t.ID, t.Name, t.Plan, t.IsolationMode, encodeTime(t.CreatedAt)); err != nil {
		if sqliteConstraint(err) {
			return tenant.Tenant{}, tenant.APIKey{}, tenant.ErrConflict
		}
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO api_keys(id, tenant_id, name, scope, prefix, key_hash, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, key.ID, key.TenantID, key.Name, key.Scope, key.Prefix, hash, encodeTime(key.CreatedAt)); err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	event, err := tenant.AuditTenantProvisioned(t, key)
	if err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	if err := tx.Commit(); err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	return t, key, nil
}

func (s *Store) ListTenants(ctx context.Context) ([]tenant.Tenant, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, name, plan, isolation_mode, created_at FROM tenants ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()

	var out []tenant.Tenant
	for rows.Next() {
		t, err := scanTenant(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

func (s *Store) Tenant(ctx context.Context, id string) (tenant.Tenant, bool, error) {
	row := s.db.QueryRowContext(ctx, `SELECT id, name, plan, isolation_mode, created_at FROM tenants WHERE id = ?`, id)
	t, err := scanTenant(row)
	if errors.Is(err, sql.ErrNoRows) {
		return tenant.Tenant{}, false, nil
	}
	if err != nil {
		return tenant.Tenant{}, false, err
	}
	return t, true, nil
}

// PromoteTenantIsolation moves the tenant up the isolation ladder (W21).
// The read, the validation, the update, and the outbox row share one
// transaction so a concurrent promotion cannot interleave a downgrade.
func (s *Store) PromoteTenantIsolation(ctx context.Context, tenantID, mode string) (tenant.Tenant, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return tenant.Tenant{}, err
	}
	defer func() { _ = tx.Rollback() }()
	current, err := scanTenant(tx.QueryRowContext(ctx,
		`SELECT id, name, plan, isolation_mode, created_at FROM tenants WHERE id = ?`, tenantID))
	if errors.Is(err, sql.ErrNoRows) {
		return tenant.Tenant{}, tenant.ErrTenantNotFound
	}
	if err != nil {
		return tenant.Tenant{}, err
	}
	if err := tenant.ValidatePromotion(current.IsolationMode, mode); err != nil {
		return tenant.Tenant{}, err
	}
	updated := current
	updated.IsolationMode = tenant.NormalizeIsolationMode(mode)
	if _, err := tx.ExecContext(ctx,
		`UPDATE tenants SET isolation_mode = ? WHERE id = ?`, updated.IsolationMode, tenantID); err != nil {
		return tenant.Tenant{}, err
	}
	event, err := tenant.AuditTenantIsolationPromoted(updated, current.IsolationMode)
	if err != nil {
		return tenant.Tenant{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.Tenant{}, err
	}
	if err := tx.Commit(); err != nil {
		return tenant.Tenant{}, err
	}
	return updated, nil
}

func (s *Store) CreateProject(ctx context.Context, p tenant.Project) (tenant.Project, error) {
	if _, ok, err := s.Tenant(ctx, p.TenantID); err != nil {
		return tenant.Project{}, err
	} else if !ok {
		return tenant.Project{}, tenant.ErrTenantNotFound
	}
	p = tenant.NormalizeProject(p)
	event, err := tenant.AuditProjectCreated(p)
	if err != nil {
		return tenant.Project{}, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return tenant.Project{}, err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO projects(id, tenant_id, name, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?)
	`, p.ID, p.TenantID, p.Name, encodeTime(p.CreatedAt), encodeTime(p.UpdatedAt)); err != nil {
		if sqliteConstraint(err) {
			return tenant.Project{}, tenant.ErrConflict
		}
		return tenant.Project{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.Project{}, err
	}
	return p, tx.Commit()
}

func (s *Store) ListProjects(ctx context.Context, tenantID string, page tenant.PageRequest) (tenant.ProjectPage, error) {
	if _, ok, err := s.Tenant(ctx, tenantID); err != nil {
		return tenant.ProjectPage{}, err
	} else if !ok {
		return tenant.ProjectPage{}, tenant.ErrTenantNotFound
	}
	page = tenant.NormalizePageRequest(page)

	rows, err := s.db.QueryContext(ctx, `
		SELECT id, tenant_id, name, created_at, updated_at
		FROM projects
		WHERE tenant_id = ? AND id > ?
		ORDER BY id
		LIMIT ?
	`, tenantID, page.Cursor, page.Limit+1)
	if err != nil {
		return tenant.ProjectPage{}, err
	}
	defer func() { _ = rows.Close() }()

	out := tenant.ProjectPage{Items: make([]tenant.Project, 0, page.Limit)}
	for rows.Next() {
		p, err := scanProject(rows)
		if err != nil {
			return tenant.ProjectPage{}, err
		}
		out.Items = append(out.Items, p)
	}
	if err := rows.Err(); err != nil {
		return tenant.ProjectPage{}, err
	}
	if len(out.Items) > page.Limit {
		out.Items = out.Items[:page.Limit]
		out.NextCursor = out.Items[len(out.Items)-1].ID
	}
	return out, nil
}

func (s *Store) Project(ctx context.Context, tenantID, projectID string) (tenant.Project, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, tenant_id, name, created_at, updated_at
		FROM projects WHERE tenant_id = ? AND id = ?
	`, tenantID, projectID)
	p, err := scanProject(row)
	if errors.Is(err, sql.ErrNoRows) {
		return tenant.Project{}, tenant.ErrProjectNotFound
	}
	return p, err
}

func (s *Store) UpdateProject(ctx context.Context, p tenant.Project) (tenant.Project, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return tenant.Project{}, err
	}
	defer func() { _ = tx.Rollback() }()
	result, err := tx.ExecContext(ctx, `
		UPDATE projects SET name = ?, updated_at = ?
		WHERE tenant_id = ? AND id = ?
	`, strings.TrimSpace(p.Name), encodeTime(time.Now().UTC()), p.TenantID, p.ID)
	if err != nil {
		return tenant.Project{}, err
	}
	if affected, err := result.RowsAffected(); err != nil {
		return tenant.Project{}, err
	} else if affected == 0 {
		return tenant.Project{}, tenant.ErrProjectNotFound
	}
	updated, err := scanProject(tx.QueryRowContext(ctx, `
		SELECT id, tenant_id, name, created_at, updated_at
		FROM projects WHERE tenant_id = ? AND id = ?
	`, p.TenantID, p.ID))
	if err != nil {
		return tenant.Project{}, err
	}
	event, err := tenant.AuditProjectUpdated(updated)
	if err != nil {
		return tenant.Project{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.Project{}, err
	}
	return updated, tx.Commit()
}

func (s *Store) DeleteProject(ctx context.Context, tenantID, projectID string) error {
	event, err := tenant.AuditProjectDeleted(tenantID, projectID)
	if err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	result, err := tx.ExecContext(ctx, `DELETE FROM projects WHERE tenant_id = ? AND id = ?`, tenantID, projectID)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil {
		return err
	} else if affected == 0 {
		return tenant.ErrProjectNotFound
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) CreateAPIKey(ctx context.Context, tenantID, name, scope string) (tenant.APIKey, error) {
	if _, ok, err := s.Tenant(ctx, tenantID); err != nil {
		return tenant.APIKey{}, err
	} else if !ok {
		return tenant.APIKey{}, tenant.ErrTenantNotFound
	}

	key, hash, err := tenant.NewRandomAPIKey(tenantID, name, scope)
	if err != nil {
		return tenant.APIKey{}, err
	}
	event, err := tenant.AuditAPIKeyCreated(key)
	if err != nil {
		return tenant.APIKey{}, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return tenant.APIKey{}, err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO api_keys(id, tenant_id, name, scope, prefix, key_hash, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, key.ID, key.TenantID, key.Name, key.Scope, key.Prefix, hash, encodeTime(key.CreatedAt)); err != nil {
		return tenant.APIKey{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.APIKey{}, err
	}
	return key, tx.Commit()
}

func (s *Store) ListAPIKeys(ctx context.Context, tenantID string) ([]tenant.APIKey, error) {
	if _, ok, err := s.Tenant(ctx, tenantID); err != nil {
		return nil, err
	} else if !ok {
		return nil, tenant.ErrTenantNotFound
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, tenant_id, name, scope, prefix, created_at, revoked_at
		FROM api_keys WHERE tenant_id = ? ORDER BY created_at, rowid
	`, tenantID)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()

	keys := make([]tenant.APIKey, 0)
	for rows.Next() {
		key, err := scanAPIKey(rows)
		if err != nil {
			return nil, err
		}
		keys = append(keys, key)
	}
	return keys, rows.Err()
}

func (s *Store) RevokeAPIKey(ctx context.Context, tenantID, keyID string) error {
	event, err := tenant.AuditAPIKeyRevoked(tenantID, keyID)
	if err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	result, err := tx.ExecContext(ctx, `
		UPDATE api_keys
		SET revoked_at = COALESCE(revoked_at, ?)
		WHERE tenant_id = ? AND id = ?
	`, encodeTime(time.Now().UTC()), tenantID, keyID)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil {
		return err
	} else if affected == 0 {
		return tenant.ErrAPIKeyNotFound
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) RotateAPIKey(ctx context.Context, tenantID, keyID, name string) (tenant.APIKey, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return tenant.APIKey{}, err
	}
	defer func() { _ = tx.Rollback() }()

	var oldName, oldScope string
	err = tx.QueryRowContext(ctx, `
		SELECT name, scope FROM api_keys
		WHERE tenant_id = ? AND id = ? AND revoked_at IS NULL
	`, tenantID, keyID).Scan(&oldName, &oldScope)
	if errors.Is(err, sql.ErrNoRows) {
		return tenant.APIKey{}, tenant.ErrAPIKeyNotFound
	}
	if err != nil {
		return tenant.APIKey{}, err
	}
	if strings.TrimSpace(name) == "" {
		name = oldName
	}
	// Rotation replaces the credential, never its authority.
	key, hash, err := tenant.NewRandomAPIKey(tenantID, name, oldScope)
	if err != nil {
		return tenant.APIKey{}, err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO api_keys(id, tenant_id, name, scope, prefix, key_hash, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, key.ID, key.TenantID, key.Name, key.Scope, key.Prefix, hash, encodeTime(key.CreatedAt)); err != nil {
		return tenant.APIKey{}, err
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE api_keys SET revoked_at = ?
		WHERE tenant_id = ? AND id = ? AND revoked_at IS NULL
	`, encodeTime(time.Now().UTC()), tenantID, keyID)
	if err != nil {
		return tenant.APIKey{}, err
	}
	if affected, err := result.RowsAffected(); err != nil {
		return tenant.APIKey{}, err
	} else if affected != 1 {
		return tenant.APIKey{}, tenant.ErrAPIKeyNotFound
	}
	event, err := tenant.AuditAPIKeyRotated(key, keyID)
	if err != nil {
		return tenant.APIKey{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.APIKey{}, err
	}
	if err := tx.Commit(); err != nil {
		return tenant.APIKey{}, err
	}
	return key, nil
}

func (s *Store) AuthenticateAPIKey(ctx context.Context, tenantID, secret string) (tenant.APIKey, error) {
	hash := tenant.HashAPIKey(secret)
	row := s.db.QueryRowContext(ctx, `
		SELECT id, tenant_id, name, scope, prefix, created_at, revoked_at
		FROM api_keys
		WHERE tenant_id = ? AND key_hash = ? AND revoked_at IS NULL
	`, tenantID, hash)
	key, err := scanAPIKey(row)
	if errors.Is(err, sql.ErrNoRows) {
		return tenant.APIKey{}, tenant.ErrUnauthorized
	}
	return key, err
}

func (s *Store) Add(ctx context.Context, e telemetry.Event) (telemetry.Event, error) {
	if _, ok, err := s.Tenant(ctx, e.TenantID); err != nil {
		return telemetry.Event{}, err
	} else if !ok {
		return telemetry.Event{}, tenant.ErrTenantNotFound
	}
	if e.Timestamp.IsZero() {
		e.Timestamp = time.Now().UTC()
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO telemetry_events(
			tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms,
			cache_hit, embedding_density, model_tier, child_spans
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, e.TenantID, e.Service, encodeTime(e.Timestamp), e.RPSWindow, e.PayloadBytes, e.LatencyMS, boolInt(e.CacheHit), e.EmbeddingDensity, e.ModelTier, e.ChildSpans)
	return e, err
}

func (s *Store) RecentByTenant(ctx context.Context, tenantID string, n int) ([]telemetry.Event, error) {
	if n <= 0 {
		n = 100
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms,
			cache_hit, embedding_density, model_tier, child_spans
		FROM telemetry_events
		WHERE tenant_id = ?
		ORDER BY timestamp DESC
		LIMIT ?
	`, tenantID, n)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()

	reversed := make([]telemetry.Event, 0, n)
	for rows.Next() {
		e, err := scanTelemetry(rows)
		if err != nil {
			return nil, err
		}
		reversed = append(reversed, e)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	for i, j := 0, len(reversed)-1; i < j; i, j = i+1, j-1 {
		reversed[i], reversed[j] = reversed[j], reversed[i]
	}
	return reversed, nil
}

func (s *Store) Features(ctx context.Context, tenantID string, query telemetry.FeatureQuery) (telemetry.FeatureSet, error) {
	if _, ok, err := s.Tenant(ctx, tenantID); err != nil {
		return telemetry.FeatureSet{}, err
	} else if !ok {
		return telemetry.FeatureSet{}, tenant.ErrTenantNotFound
	}
	query = telemetry.NormalizeFeatureQuery(query)

	sqlQuery := `
		SELECT tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms,
			cache_hit, embedding_density, model_tier, child_spans
		FROM telemetry_events
		WHERE tenant_id = ? AND timestamp >= ? AND timestamp < ?
	`
	args := []any{tenantID, encodeTime(query.Since), encodeTime(query.Until)}
	if query.Service != "" {
		sqlQuery += ` AND service = ?`
		args = append(args, query.Service)
	}
	sqlQuery += ` ORDER BY timestamp LIMIT ?`
	args = append(args, telemetry.MaxFeatureEvents)

	rows, err := s.db.QueryContext(ctx, sqlQuery, args...)
	if err != nil {
		return telemetry.FeatureSet{}, err
	}
	defer func() { _ = rows.Close() }()

	events := make([]telemetry.Event, 0)
	for rows.Next() {
		event, err := scanTelemetry(rows)
		if err != nil {
			return telemetry.FeatureSet{}, err
		}
		events = append(events, event)
	}
	if err := rows.Err(); err != nil {
		return telemetry.FeatureSet{}, err
	}
	return telemetry.BuildFeatureSet(tenantID, query, events), nil
}

type rowScanner interface {
	Scan(dest ...any) error
}

func scanTenant(row rowScanner) (tenant.Tenant, error) {
	var t tenant.Tenant
	var created string
	if err := row.Scan(&t.ID, &t.Name, &t.Plan, &t.IsolationMode, &created); err != nil {
		return tenant.Tenant{}, err
	}
	var err error
	t.CreatedAt, err = decodeTime(created)
	return t, err
}

func scanProject(row rowScanner) (tenant.Project, error) {
	var p tenant.Project
	var created, updated string
	if err := row.Scan(&p.ID, &p.TenantID, &p.Name, &created, &updated); err != nil {
		return tenant.Project{}, err
	}
	var err error
	p.CreatedAt, err = decodeTime(created)
	if err != nil {
		return tenant.Project{}, err
	}
	p.UpdatedAt, err = decodeTime(updated)
	return p, err
}

func scanAPIKey(row rowScanner) (tenant.APIKey, error) {
	var key tenant.APIKey
	var created string
	var revoked sql.NullString
	if err := row.Scan(&key.ID, &key.TenantID, &key.Name, &key.Scope, &key.Prefix, &created, &revoked); err != nil {
		return tenant.APIKey{}, err
	}
	var err error
	key.CreatedAt, err = decodeTime(created)
	if err != nil {
		return tenant.APIKey{}, err
	}
	if revoked.Valid {
		value, err := decodeTime(revoked.String)
		if err != nil {
			return tenant.APIKey{}, err
		}
		key.RevokedAt = &value
	}
	return key, err
}

func scanTelemetry(row rowScanner) (telemetry.Event, error) {
	var e telemetry.Event
	var timestamp string
	var cacheHit int
	if err := row.Scan(
		&e.TenantID,
		&e.Service,
		&timestamp,
		&e.RPSWindow,
		&e.PayloadBytes,
		&e.LatencyMS,
		&cacheHit,
		&e.EmbeddingDensity,
		&e.ModelTier,
		&e.ChildSpans,
	); err != nil {
		return telemetry.Event{}, err
	}
	var err error
	e.Timestamp, err = decodeTime(timestamp)
	e.CacheHit = cacheHit == 1
	return e, err
}

func encodeTime(t time.Time) string {
	return t.UTC().Format(time.RFC3339Nano)
}

func decodeTime(s string) (time.Time, error) {
	return time.Parse(time.RFC3339Nano, s)
}

func boolInt(v bool) int {
	if v {
		return 1
	}
	return 0
}

func sqliteConstraint(err error) bool {
	return err != nil && (strings.Contains(err.Error(), "constraint failed") || strings.Contains(err.Error(), "FOREIGN KEY constraint failed"))
}
