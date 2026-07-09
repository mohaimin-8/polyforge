package postgres

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"

	"polyforge/internal/events"
	"polyforge/internal/saga"
	"polyforge/internal/tenant"
)

var _ events.Outbox = (*Store)(nil)
var _ saga.StateStore = (*Store)(nil)

// insertOutbox writes one outbox row on the caller's transaction, making
// the domain write and its audit event atomic (ADR 0006). ON CONFLICT DO
// NOTHING on the unique event ID makes deterministic-ID appends idempotent.
func insertOutbox(ctx context.Context, tx pgx.Tx, e events.Event) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO outbox(id, tenant_id, action, subject, occurred_at, payload)
		VALUES ($1, $2, $3, $4, $5, $6)
		ON CONFLICT (id) DO NOTHING
	`, e.ID, e.TenantID, e.Action, e.Subject, e.OccurredAt, e.Payload)
	return err
}

// AppendEvent records a standalone event (saga event-only steps) through the
// admin role: it is not tied to any tenant transaction.
func (s *Store) AppendEvent(ctx context.Context, e events.Event) error {
	_, err := s.admin.Exec(ctx, `
		INSERT INTO outbox(id, tenant_id, action, subject, occurred_at, payload)
		VALUES ($1, $2, $3, $4, $5, $6)
		ON CONFLICT (id) DO NOTHING
	`, e.ID, e.TenantID, e.Action, e.Subject, e.OccurredAt, e.Payload)
	return err
}

func (s *Store) Unpublished(ctx context.Context, limit int) ([]events.Event, error) {
	rows, err := s.admin.Query(ctx, `
		SELECT id, tenant_id, action, subject, occurred_at, payload
		FROM outbox WHERE published_at IS NULL
		ORDER BY seq LIMIT $1
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make([]events.Event, 0, limit)
	for rows.Next() {
		var e events.Event
		if err := rows.Scan(&e.ID, &e.TenantID, &e.Action, &e.Subject, &e.OccurredAt, &e.Payload); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

func (s *Store) MarkPublished(ctx context.Context, ids []string, at time.Time) error {
	if len(ids) == 0 {
		return nil
	}
	_, err := s.admin.Exec(ctx, `
		UPDATE outbox SET published_at = $1
		WHERE id = ANY($2) AND published_at IS NULL
	`, at, ids)
	return err
}

// DeleteTenant removes the tenant and, via ON DELETE CASCADE, everything it
// owns. It exists for the onboarding saga's compensation path and runs as
// admin: RLS scopes the app role to one tenant's rows, but dropping the
// tenant itself is a control-plane action.
func (s *Store) DeleteTenant(ctx context.Context, id string) error {
	event, err := tenant.AuditTenantDeleted(id)
	if err != nil {
		return err
	}
	tx, err := s.admin.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	tag, err := tx.Exec(ctx, `DELETE FROM tenants WHERE id = $1`, id)
	if err != nil {
		return mapPostgresError(err)
	}
	if tag.RowsAffected() == 0 {
		return tenant.ErrTenantNotFound
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) SaveSagaState(ctx context.Context, state saga.State) error {
	_, err := s.admin.Exec(ctx, `
		INSERT INTO sagas(id, name, tenant_id, status, steps_done, error, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (id) DO UPDATE SET
			status = excluded.status,
			steps_done = excluded.steps_done,
			error = excluded.error,
			updated_at = excluded.updated_at
	`, state.ID, state.Name, state.TenantID, string(state.Status), state.StepsDone, state.Error, state.UpdatedAt)
	return err
}

func (s *Store) LoadSagaState(ctx context.Context, id string) (saga.State, bool, error) {
	var state saga.State
	var status string
	err := s.admin.QueryRow(ctx, `
		SELECT id, name, tenant_id, status, steps_done, error, updated_at
		FROM sagas WHERE id = $1
	`, id).Scan(&state.ID, &state.Name, &state.TenantID, &status, &state.StepsDone, &state.Error, &state.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return saga.State{}, false, nil
	}
	if err != nil {
		return saga.State{}, false, err
	}
	state.Status = saga.Status(status)
	return state, true, nil
}
