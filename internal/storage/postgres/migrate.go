package postgres

import (
	"context"
	"embed"
	"fmt"
	"io/fs"
	"path/filepath"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

//go:embed migrations/*.sql
var migrationFiles embed.FS

const migrationLockID int64 = 746912357831

func (s *Store) Migrate(ctx context.Context) error {
	conn, err := s.admin.Acquire(ctx)
	if err != nil {
		return fmt.Errorf("acquire migration connection: %w", err)
	}
	defer conn.Release()

	tx, err := conn.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin migration: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock($1)`, migrationLockID); err != nil {
		return fmt.Errorf("lock migrations: %w", err)
	}
	if _, err := tx.Exec(ctx, `
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version TEXT PRIMARY KEY,
			applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
		)
	`); err != nil {
		return fmt.Errorf("create migration ledger: %w", err)
	}

	entries, err := fs.ReadDir(migrationFiles, "migrations")
	if err != nil {
		return fmt.Errorf("read embedded migrations: %w", err)
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".sql" {
			continue
		}
		version := strings.TrimSuffix(entry.Name(), ".sql")
		var applied bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = $1)`, version).Scan(&applied); err != nil {
			return fmt.Errorf("check migration %s: %w", version, err)
		}
		if applied {
			continue
		}
		contents, err := migrationFiles.ReadFile("migrations/" + entry.Name())
		if err != nil {
			return fmt.Errorf("read migration %s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, string(contents), pgx.QueryExecModeSimpleProtocol); err != nil {
			return fmt.Errorf("apply migration %s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, `INSERT INTO schema_migrations(version) VALUES ($1)`, version); err != nil {
			return fmt.Errorf("record migration %s: %w", version, err)
		}
	}
	// Grants run INSIDE the migration transaction, under the advisory lock
	// taken above. They used to run after Commit, through the pool, which put
	// them outside the lock -- so every replica issued them concurrently.
	//
	// PostgreSQL serialises DDL on a role through its catalog rows, and
	// concurrent GRANTs on the same objects collide with
	// "tuple concurrently updated (SQLSTATE XX000)". The losing backend exits
	// non-zero, so the pod crash-loops. It is a startup race that only appears
	// with several replicas coming up together: found by WP14 Stage B, where
	// 16 control-plane replicas started at once and one died on it, dropping
	// the connection that was provisioning tenants and failing the stage 167
	// seconds in. A single-replica deployment never sees it.
	if err := s.grantAppRole(ctx, tx); err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit migrations: %w", err)
	}
	return nil
}

// grantAppRole issues the application role's grants. It takes the querier so
// the caller decides the transaction, which is what keeps these under the
// migration advisory lock rather than racing outside it.
func (s *Store) grantAppRole(ctx context.Context, q interface {
	Exec(context.Context, string, ...any) (pgconn.CommandTag, error)
}) error {
	role := pgx.Identifier{s.appRole}.Sanitize()
	statements := []string{
		`GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, projects, api_keys, telemetry_events TO ` + role,
		// The app role only appends to the outbox; reading and stamping
		// published_at is the relay's job through the admin role.
		`GRANT INSERT ON outbox TO ` + role,
		`GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ` + role,
	}
	for _, statement := range statements {
		if _, err := q.Exec(ctx, statement); err != nil {
			return fmt.Errorf("grant application role %s: %w", s.appRole, err)
		}
	}
	return nil
}
