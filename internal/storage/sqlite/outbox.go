package sqlite

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"

	"polyforge/internal/events"
	"polyforge/internal/saga"
	"polyforge/internal/tenant"
)

var _ events.Outbox = (*Store)(nil)
var _ saga.StateStore = (*Store)(nil)

type sqlExecer interface {
	ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
}

// insertOutbox writes one outbox row on the caller's transaction, which is
// what makes the domain write and its audit event atomic (ADR 0006).
// INSERT OR IGNORE on the unique event ID makes deterministic-ID appends
// (the saga's) idempotent under crash-retry.
func insertOutbox(ctx context.Context, ex sqlExecer, e events.Event) error {
	_, err := ex.ExecContext(ctx, `
		INSERT OR IGNORE INTO outbox(id, tenant_id, action, subject, occurred_at, payload)
		VALUES (?, ?, ?, ?, ?, ?)
	`, e.ID, e.TenantID, e.Action, e.Subject, encodeTime(e.OccurredAt), string(e.Payload))
	return err
}

// AppendEvent records a standalone event outside any domain write; the
// saga's event-only steps use it.
func (s *Store) AppendEvent(ctx context.Context, e events.Event) error {
	return insertOutbox(ctx, s.db, e)
}

func (s *Store) Unpublished(ctx context.Context, limit int) ([]events.Event, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, tenant_id, action, subject, occurred_at, payload
		FROM outbox WHERE published_at IS NULL
		ORDER BY seq LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()

	out := make([]events.Event, 0, limit)
	for rows.Next() {
		var e events.Event
		var occurred, payload string
		if err := rows.Scan(&e.ID, &e.TenantID, &e.Action, &e.Subject, &occurred, &payload); err != nil {
			return nil, err
		}
		if e.OccurredAt, err = decodeTime(occurred); err != nil {
			return nil, err
		}
		e.Payload = []byte(payload)
		out = append(out, e)
	}
	return out, rows.Err()
}

func (s *Store) MarkPublished(ctx context.Context, ids []string, at time.Time) error {
	if len(ids) == 0 {
		return nil
	}
	placeholders := strings.Repeat("?,", len(ids))
	args := make([]any, 0, len(ids)+1)
	args = append(args, encodeTime(at))
	for _, id := range ids {
		args = append(args, id)
	}
	_, err := s.db.ExecContext(ctx, `
		UPDATE outbox SET published_at = ?
		WHERE id IN (`+placeholders[:len(placeholders)-1]+`) AND published_at IS NULL
	`, args...)
	return err
}

// DeleteTenant removes the tenant and, via ON DELETE CASCADE, everything it
// owns. It exists for the onboarding saga's compensation path. The audit
// event survives because the outbox has no foreign key to tenants: the
// stream must outlive its subjects.
func (s *Store) DeleteTenant(ctx context.Context, id string) error {
	event, err := tenant.AuditTenantDeleted(id)
	if err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	result, err := tx.ExecContext(ctx, `DELETE FROM tenants WHERE id = ?`, id)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil {
		return err
	} else if affected == 0 {
		return tenant.ErrTenantNotFound
	}
	if err := insertOutbox(ctx, tx, event); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) SaveSagaState(ctx context.Context, state saga.State) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO sagas(id, name, tenant_id, status, steps_done, error, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			status = excluded.status,
			steps_done = excluded.steps_done,
			error = excluded.error,
			updated_at = excluded.updated_at
	`, state.ID, state.Name, state.TenantID, string(state.Status), state.StepsDone, state.Error, encodeTime(state.UpdatedAt))
	return err
}

func (s *Store) LoadSagaState(ctx context.Context, id string) (saga.State, bool, error) {
	var state saga.State
	var status, updated string
	err := s.db.QueryRowContext(ctx, `
		SELECT id, name, tenant_id, status, steps_done, error, updated_at
		FROM sagas WHERE id = ?
	`, id).Scan(&state.ID, &state.Name, &state.TenantID, &status, &state.StepsDone, &state.Error, &updated)
	if errors.Is(err, sql.ErrNoRows) {
		return saga.State{}, false, nil
	}
	if err != nil {
		return saga.State{}, false, err
	}
	state.Status = saga.Status(status)
	state.UpdatedAt, err = decodeTime(updated)
	return state, err == nil, err
}
