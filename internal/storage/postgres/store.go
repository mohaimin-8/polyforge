package postgres

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

type Config struct {
	AdminURL string
	AppURL   string
}

type Store struct {
	admin   *pgxpool.Pool
	app     *pgxpool.Pool
	appRole string
}

var _ tenant.Repository = (*Store)(nil)
var _ telemetry.Repository = (*Store)(nil)

func Open(ctx context.Context, cfg Config) (*Store, error) {
	if strings.TrimSpace(cfg.AdminURL) == "" || strings.TrimSpace(cfg.AppURL) == "" {
		return nil, errors.New("both PostgreSQL admin and app URLs are required")
	}
	admin, err := pgxpool.New(ctx, cfg.AdminURL)
	if err != nil {
		return nil, fmt.Errorf("configure PostgreSQL admin pool: %w", err)
	}
	app, err := pgxpool.New(ctx, cfg.AppURL)
	if err != nil {
		admin.Close()
		return nil, fmt.Errorf("configure PostgreSQL app pool: %w", err)
	}
	s := &Store{admin: admin, app: app}
	if err := s.verifyConnectionsAndRoles(ctx); err != nil {
		s.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() {
	s.app.Close()
	s.admin.Close()
}

func (s *Store) verifyConnectionsAndRoles(ctx context.Context) error {
	if err := s.admin.Ping(ctx); err != nil {
		return fmt.Errorf("ping PostgreSQL admin pool: %w", err)
	}
	if err := s.app.Ping(ctx); err != nil {
		return fmt.Errorf("ping PostgreSQL app pool: %w", err)
	}
	adminRole, adminBypass, err := roleSecurity(ctx, s.admin)
	if err != nil {
		return fmt.Errorf("inspect PostgreSQL admin role: %w", err)
	}
	appRole, appBypass, err := roleSecurity(ctx, s.app)
	if err != nil {
		return fmt.Errorf("inspect PostgreSQL app role: %w", err)
	}
	if adminRole == appRole {
		return errors.New("PostgreSQL admin and app connections must use distinct roles")
	}
	if !adminBypass {
		return fmt.Errorf("PostgreSQL admin role %q must be superuser or have BYPASSRLS", adminRole)
	}
	if appBypass {
		return fmt.Errorf("PostgreSQL app role %q must not be superuser or have BYPASSRLS", appRole)
	}
	s.appRole = appRole
	return nil
}

func roleSecurity(ctx context.Context, pool *pgxpool.Pool) (string, bool, error) {
	var role string
	var bypass bool
	err := pool.QueryRow(ctx, `
		SELECT rolname, rolsuper OR rolbypassrls
		FROM pg_roles WHERE rolname = current_user
	`).Scan(&role, &bypass)
	return role, bypass, err
}

func (s *Store) CreateTenant(ctx context.Context, t tenant.Tenant) (tenant.Tenant, error) {
	t = tenant.NormalizeTenant(t)
	event, err := tenant.AuditTenantCreated(t)
	if err != nil {
		return tenant.Tenant{}, err
	}
	tx, err := s.admin.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return tenant.Tenant{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, `
		INSERT INTO tenants(id, name, plan, isolation_mode, created_at)
		VALUES ($1, $2, $3, $4, $5)
	`, t.ID, t.Name, t.Plan, t.IsolationMode, t.CreatedAt); err != nil {
		return tenant.Tenant{}, mapPostgresError(err)
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.Tenant{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return tenant.Tenant{}, err
	}
	return t, nil
}

func (s *Store) ProvisionTenant(ctx context.Context, t tenant.Tenant, keyName string) (tenant.Tenant, tenant.APIKey, error) {
	t = tenant.NormalizeTenant(t)
	key, hash, err := tenant.NewRandomAPIKey(t.ID, keyName, tenant.ScopeFull)
	if err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	tx, err := s.admin.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, `
		INSERT INTO tenants(id, name, plan, isolation_mode, created_at)
		VALUES ($1, $2, $3, $4, $5)
	`, t.ID, t.Name, t.Plan, t.IsolationMode, t.CreatedAt); err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, mapPostgresError(err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO api_keys(id, tenant_id, name, scope, prefix, key_hash, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
	`, key.ID, key.TenantID, key.Name, key.Scope, key.Prefix, hash, key.CreatedAt); err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, mapPostgresError(err)
	}
	event, err := tenant.AuditTenantProvisioned(t, key)
	if err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return tenant.Tenant{}, tenant.APIKey{}, err
	}
	return t, key, nil
}

func (s *Store) ListTenants(ctx context.Context) ([]tenant.Tenant, error) {
	rows, err := s.admin.Query(ctx, `
		SELECT id, name, plan, isolation_mode, created_at FROM tenants ORDER BY id
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	tenants := make([]tenant.Tenant, 0)
	for rows.Next() {
		t, err := scanTenant(rows)
		if err != nil {
			return nil, err
		}
		tenants = append(tenants, t)
	}
	return tenants, rows.Err()
}

func (s *Store) Tenant(ctx context.Context, id string) (tenant.Tenant, bool, error) {
	type lookup struct {
		tenant tenant.Tenant
		found  bool
	}
	result, err := withTenantTx(ctx, s.app, id, func(tx pgx.Tx) (lookup, error) {
		t, err := scanTenant(tx.QueryRow(ctx, `
			SELECT id, name, plan, isolation_mode, created_at FROM tenants WHERE id = $1
		`, id))
		if errors.Is(err, pgx.ErrNoRows) {
			return lookup{}, nil
		}
		return lookup{tenant: t, found: err == nil}, err
	})
	return result.tenant, result.found, err
}

// PromoteTenantIsolation moves the tenant up the isolation ladder (W21).
// It runs on the admin pool like every tenants-table write: promotion is an
// operator action, not something a tenant credential can reach.
func (s *Store) PromoteTenantIsolation(ctx context.Context, tenantID, mode string) (tenant.Tenant, error) {
	tx, err := s.admin.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return tenant.Tenant{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	current, err := scanTenant(tx.QueryRow(ctx, `
		SELECT id, name, plan, isolation_mode, created_at FROM tenants WHERE id = $1 FOR UPDATE
	`, tenantID))
	if errors.Is(err, pgx.ErrNoRows) {
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
	if _, err := tx.Exec(ctx, `
		UPDATE tenants SET isolation_mode = $1 WHERE id = $2
	`, updated.IsolationMode, tenantID); err != nil {
		return tenant.Tenant{}, err
	}
	event, err := tenant.AuditTenantIsolationPromoted(updated, current.IsolationMode)
	if err != nil {
		return tenant.Tenant{}, err
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return tenant.Tenant{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return tenant.Tenant{}, err
	}
	return updated, nil
}

func (s *Store) CreateProject(ctx context.Context, p tenant.Project) (tenant.Project, error) {
	p = tenant.NormalizeProject(p)
	return withTenantTx(ctx, s.app, p.TenantID, func(tx pgx.Tx) (tenant.Project, error) {
		if err := requireTenant(ctx, tx, p.TenantID); err != nil {
			return tenant.Project{}, err
		}
		_, err := tx.Exec(ctx, `
			INSERT INTO projects(id, tenant_id, name, created_at, updated_at)
			VALUES ($1, $2, $3, $4, $5)
		`, p.ID, p.TenantID, p.Name, p.CreatedAt, p.UpdatedAt)
		if err != nil {
			return tenant.Project{}, mapPostgresError(err)
		}
		event, err := tenant.AuditProjectCreated(p)
		if err != nil {
			return tenant.Project{}, err
		}
		return p, insertOutbox(ctx, tx, event)
	})
}

func (s *Store) ListProjects(ctx context.Context, tenantID string, page tenant.PageRequest) (tenant.ProjectPage, error) {
	page = tenant.NormalizePageRequest(page)
	return withTenantTx(ctx, s.app, tenantID, func(tx pgx.Tx) (tenant.ProjectPage, error) {
		if err := requireTenant(ctx, tx, tenantID); err != nil {
			return tenant.ProjectPage{}, err
		}
		rows, err := tx.Query(ctx, `
			SELECT id, tenant_id, name, created_at, updated_at
			FROM projects
			WHERE tenant_id = $1 AND id > $2
			ORDER BY id LIMIT $3
		`, tenantID, page.Cursor, page.Limit+1)
		if err != nil {
			return tenant.ProjectPage{}, err
		}
		defer rows.Close()
		result := tenant.ProjectPage{Items: make([]tenant.Project, 0, page.Limit)}
		for rows.Next() {
			p, err := scanProject(rows)
			if err != nil {
				return tenant.ProjectPage{}, err
			}
			result.Items = append(result.Items, p)
		}
		if err := rows.Err(); err != nil {
			return tenant.ProjectPage{}, err
		}
		if len(result.Items) > page.Limit {
			result.Items = result.Items[:page.Limit]
			result.NextCursor = result.Items[len(result.Items)-1].ID
		}
		return result, nil
	})
}

func (s *Store) Project(ctx context.Context, tenantID, projectID string) (tenant.Project, error) {
	return withTenantTx(ctx, s.app, tenantID, func(tx pgx.Tx) (tenant.Project, error) {
		p, err := scanProject(tx.QueryRow(ctx, `
			SELECT id, tenant_id, name, created_at, updated_at
			FROM projects WHERE tenant_id = $1 AND id = $2
		`, tenantID, projectID))
		if errors.Is(err, pgx.ErrNoRows) {
			return tenant.Project{}, tenant.ErrProjectNotFound
		}
		return p, err
	})
}

func (s *Store) UpdateProject(ctx context.Context, p tenant.Project) (tenant.Project, error) {
	return withTenantTx(ctx, s.app, p.TenantID, func(tx pgx.Tx) (tenant.Project, error) {
		var updated tenant.Project
		err := tx.QueryRow(ctx, `
			UPDATE projects SET name = $1, updated_at = now()
			WHERE tenant_id = $2 AND id = $3
			RETURNING id, tenant_id, name, created_at, updated_at
		`, strings.TrimSpace(p.Name), p.TenantID, p.ID).Scan(
			&updated.ID, &updated.TenantID, &updated.Name, &updated.CreatedAt, &updated.UpdatedAt,
		)
		if errors.Is(err, pgx.ErrNoRows) {
			return tenant.Project{}, tenant.ErrProjectNotFound
		}
		if err != nil {
			return tenant.Project{}, err
		}
		event, err := tenant.AuditProjectUpdated(updated)
		if err != nil {
			return tenant.Project{}, err
		}
		return updated, insertOutbox(ctx, tx, event)
	})
}

func (s *Store) DeleteProject(ctx context.Context, tenantID, projectID string) error {
	return withTenantTxExec(ctx, s.app, tenantID, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `DELETE FROM projects WHERE tenant_id = $1 AND id = $2`, tenantID, projectID)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return tenant.ErrProjectNotFound
		}
		event, err := tenant.AuditProjectDeleted(tenantID, projectID)
		if err != nil {
			return err
		}
		return insertOutbox(ctx, tx, event)
	})
}

func (s *Store) CreateAPIKey(ctx context.Context, tenantID, name, scope string) (tenant.APIKey, error) {
	key, hash, err := tenant.NewRandomAPIKey(tenantID, name, scope)
	if err != nil {
		return tenant.APIKey{}, err
	}
	return withTenantTx(ctx, s.app, tenantID, func(tx pgx.Tx) (tenant.APIKey, error) {
		if err := requireTenant(ctx, tx, tenantID); err != nil {
			return tenant.APIKey{}, err
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO api_keys(id, tenant_id, name, scope, prefix, key_hash, created_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7)
		`, key.ID, key.TenantID, key.Name, key.Scope, key.Prefix, hash, key.CreatedAt); err != nil {
			return tenant.APIKey{}, mapPostgresError(err)
		}
		event, err := tenant.AuditAPIKeyCreated(key)
		if err != nil {
			return tenant.APIKey{}, err
		}
		return key, insertOutbox(ctx, tx, event)
	})
}

func (s *Store) ListAPIKeys(ctx context.Context, tenantID string) ([]tenant.APIKey, error) {
	return withTenantTx(ctx, s.app, tenantID, func(tx pgx.Tx) ([]tenant.APIKey, error) {
		if err := requireTenant(ctx, tx, tenantID); err != nil {
			return nil, err
		}
		rows, err := tx.Query(ctx, `
			SELECT id, tenant_id, name, scope, prefix, created_at, revoked_at
			FROM api_keys WHERE tenant_id = $1 ORDER BY created_at, id
		`, tenantID)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		keys := make([]tenant.APIKey, 0)
		for rows.Next() {
			key, err := scanAPIKey(rows)
			if err != nil {
				return nil, err
			}
			keys = append(keys, key)
		}
		return keys, rows.Err()
	})
}

func (s *Store) RevokeAPIKey(ctx context.Context, tenantID, keyID string) error {
	return withTenantTxExec(ctx, s.app, tenantID, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
			UPDATE api_keys SET revoked_at = COALESCE(revoked_at, now())
			WHERE tenant_id = $1 AND id = $2
		`, tenantID, keyID)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return tenant.ErrAPIKeyNotFound
		}
		event, err := tenant.AuditAPIKeyRevoked(tenantID, keyID)
		if err != nil {
			return err
		}
		return insertOutbox(ctx, tx, event)
	})
}

func (s *Store) RotateAPIKey(ctx context.Context, tenantID, keyID, name string) (tenant.APIKey, error) {
	return withTenantTx(ctx, s.app, tenantID, func(tx pgx.Tx) (tenant.APIKey, error) {
		var oldName, oldScope string
		err := tx.QueryRow(ctx, `
			SELECT name, scope FROM api_keys
			WHERE tenant_id = $1 AND id = $2 AND revoked_at IS NULL
		`, tenantID, keyID).Scan(&oldName, &oldScope)
		if errors.Is(err, pgx.ErrNoRows) {
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
		if _, err := tx.Exec(ctx, `
			INSERT INTO api_keys(id, tenant_id, name, scope, prefix, key_hash, created_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7)
		`, key.ID, key.TenantID, key.Name, key.Scope, key.Prefix, hash, key.CreatedAt); err != nil {
			return tenant.APIKey{}, mapPostgresError(err)
		}
		tag, err := tx.Exec(ctx, `
			UPDATE api_keys SET revoked_at = now()
			WHERE tenant_id = $1 AND id = $2 AND revoked_at IS NULL
		`, tenantID, keyID)
		if err != nil {
			return tenant.APIKey{}, err
		}
		if tag.RowsAffected() != 1 {
			return tenant.APIKey{}, tenant.ErrAPIKeyNotFound
		}
		event, err := tenant.AuditAPIKeyRotated(key, keyID)
		if err != nil {
			return tenant.APIKey{}, err
		}
		return key, insertOutbox(ctx, tx, event)
	})
}

func (s *Store) AuthenticateAPIKey(ctx context.Context, tenantID, secret string) (tenant.APIKey, error) {
	hash := tenant.HashAPIKey(secret)
	return withTenantTx(ctx, s.app, tenantID, func(tx pgx.Tx) (tenant.APIKey, error) {
		key, err := scanAPIKey(tx.QueryRow(ctx, `
			SELECT id, tenant_id, name, scope, prefix, created_at, revoked_at
			FROM api_keys
			WHERE tenant_id = $1 AND key_hash = $2 AND revoked_at IS NULL
		`, tenantID, hash))
		if errors.Is(err, pgx.ErrNoRows) {
			return tenant.APIKey{}, tenant.ErrUnauthorized
		}
		return key, err
	})
}

func (s *Store) Add(ctx context.Context, e telemetry.Event) (telemetry.Event, error) {
	if e.Timestamp.IsZero() {
		e.Timestamp = time.Now().UTC()
	}
	return withTenantTx(ctx, s.app, e.TenantID, func(tx pgx.Tx) (telemetry.Event, error) {
		if err := requireTenant(ctx, tx, e.TenantID); err != nil {
			return telemetry.Event{}, err
		}
		_, err := tx.Exec(ctx, `
			INSERT INTO telemetry_events(
				tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms,
				cache_hit, embedding_density, model_tier, child_spans
			) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
		`, e.TenantID, e.Service, e.Timestamp, e.RPSWindow, e.PayloadBytes, e.LatencyMS,
			e.CacheHit, e.EmbeddingDensity, e.ModelTier, e.ChildSpans)
		return e, mapPostgresError(err)
	})
}

func (s *Store) RecentByTenant(ctx context.Context, tenantID string, n int) ([]telemetry.Event, error) {
	if n <= 0 {
		n = 100
	}
	return withTenantTx(ctx, s.app, tenantID, func(tx pgx.Tx) ([]telemetry.Event, error) {
		rows, err := tx.Query(ctx, `
			SELECT tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms,
				cache_hit, embedding_density, model_tier, child_spans
			FROM telemetry_events WHERE tenant_id = $1
			ORDER BY timestamp DESC LIMIT $2
		`, tenantID, n)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		events := make([]telemetry.Event, 0, n)
		for rows.Next() {
			e, err := scanTelemetry(rows)
			if err != nil {
				return nil, err
			}
			events = append(events, e)
		}
		if err := rows.Err(); err != nil {
			return nil, err
		}
		for i, j := 0, len(events)-1; i < j; i, j = i+1, j-1 {
			events[i], events[j] = events[j], events[i]
		}
		return events, nil
	})
}

func (s *Store) Features(ctx context.Context, tenantID string, query telemetry.FeatureQuery) (telemetry.FeatureSet, error) {
	query = telemetry.NormalizeFeatureQuery(query)
	return withTenantTx(ctx, s.app, tenantID, func(tx pgx.Tx) (telemetry.FeatureSet, error) {
		if err := requireTenant(ctx, tx, tenantID); err != nil {
			return telemetry.FeatureSet{}, err
		}
		sqlQuery := `
			SELECT tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms,
				cache_hit, embedding_density, model_tier, child_spans
			FROM telemetry_events
			WHERE tenant_id = $1 AND timestamp >= $2 AND timestamp < $3
		`
		args := []any{tenantID, query.Since, query.Until}
		if query.Service != "" {
			args = append(args, query.Service)
			sqlQuery += fmt.Sprintf(` AND service = $%d`, len(args))
		}
		args = append(args, telemetry.MaxFeatureEvents+1)
		sqlQuery += fmt.Sprintf(` ORDER BY timestamp LIMIT $%d`, len(args))

		rows, err := tx.Query(ctx, sqlQuery, args...)
		if err != nil {
			return telemetry.FeatureSet{}, err
		}
		defer rows.Close()

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
		events, truncated := telemetry.ClampFeatureEvents(events)
		set := telemetry.BuildFeatureSet(tenantID, query, events)
		set.Truncated = truncated
		return set, nil
	})
}

type rowScanner interface {
	Scan(dest ...any) error
}

func scanTenant(row rowScanner) (tenant.Tenant, error) {
	var t tenant.Tenant
	err := row.Scan(&t.ID, &t.Name, &t.Plan, &t.IsolationMode, &t.CreatedAt)
	return t, err
}

func scanProject(row rowScanner) (tenant.Project, error) {
	var p tenant.Project
	err := row.Scan(&p.ID, &p.TenantID, &p.Name, &p.CreatedAt, &p.UpdatedAt)
	return p, err
}

func scanAPIKey(row rowScanner) (tenant.APIKey, error) {
	var key tenant.APIKey
	var revoked pgtype.Timestamptz
	if err := row.Scan(&key.ID, &key.TenantID, &key.Name, &key.Scope, &key.Prefix, &key.CreatedAt, &revoked); err != nil {
		return tenant.APIKey{}, err
	}
	if revoked.Valid {
		value := revoked.Time
		key.RevokedAt = &value
	}
	return key, nil
}

func scanTelemetry(row rowScanner) (telemetry.Event, error) {
	var e telemetry.Event
	err := row.Scan(
		&e.TenantID, &e.Service, &e.Timestamp, &e.RPSWindow, &e.PayloadBytes,
		&e.LatencyMS, &e.CacheHit, &e.EmbeddingDensity, &e.ModelTier, &e.ChildSpans,
	)
	return e, err
}

func requireTenant(ctx context.Context, tx pgx.Tx, tenantID string) error {
	var exists bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM tenants WHERE id = $1)`, tenantID).Scan(&exists); err != nil {
		return err
	}
	if !exists {
		return tenant.ErrTenantNotFound
	}
	return nil
}

func setTenant(ctx context.Context, tx pgx.Tx, tenantID string) error {
	var configured string
	if err := tx.QueryRow(ctx, `SELECT set_config('app.tenant_id', $1, true)`, tenantID).Scan(&configured); err != nil {
		return err
	}
	if configured != tenantID {
		return errors.New("failed to establish PostgreSQL tenant context")
	}
	return nil
}

func withTenantTx[T any](ctx context.Context, pool *pgxpool.Pool, tenantID string, fn func(pgx.Tx) (T, error)) (T, error) {
	var zero T
	tx, err := pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return zero, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err := setTenant(ctx, tx, tenantID); err != nil {
		return zero, err
	}
	value, err := fn(tx)
	if err != nil {
		return zero, err
	}
	if err := tx.Commit(ctx); err != nil {
		return zero, err
	}
	return value, nil
}

func withTenantTxExec(ctx context.Context, pool *pgxpool.Pool, tenantID string, fn func(pgx.Tx) error) error {
	_, err := withTenantTx(ctx, pool, tenantID, func(tx pgx.Tx) (struct{}, error) {
		return struct{}{}, fn(tx)
	})
	return err
}

func mapPostgresError(err error) error {
	if err == nil {
		return nil
	}
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) {
		return err
	}
	switch pgErr.Code {
	case "23505":
		return tenant.ErrConflict
	case "23503":
		return tenant.ErrTenantNotFound
	default:
		return err
	}
}
