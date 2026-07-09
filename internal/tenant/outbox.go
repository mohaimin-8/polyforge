package tenant

import (
	"context"
	"time"

	"polyforge/internal/events"
)

// The memory store's transactional outbox: rows are appended under the same
// mutex as the domain write, which is this store's equivalent of "same
// transaction" (ADR 0006). The relay drains it via events.Outbox.

type outboxRow struct {
	event     events.Event
	published bool
}

var _ events.Outbox = (*Store)(nil)

func (s *Store) appendOutboxLocked(e events.Event) {
	if _, exists := s.outboxIDs[e.ID]; exists {
		return
	}
	s.outboxIDs[e.ID] = struct{}{}
	s.outbox = append(s.outbox, outboxRow{event: e})
}

// AppendEvent records a standalone event (the saga's event-only steps use
// this). Appending an ID that already exists is a no-op, so retries after a
// crash are idempotent.
func (s *Store) AppendEvent(_ context.Context, e events.Event) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.appendOutboxLocked(e)
	return nil
}

func (s *Store) Unpublished(_ context.Context, limit int) ([]events.Event, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]events.Event, 0, limit)
	for i := range s.outbox {
		if s.outbox[i].published {
			continue
		}
		out = append(out, s.outbox[i].event)
		if len(out) == limit {
			break
		}
	}
	return out, nil
}

func (s *Store) MarkPublished(_ context.Context, ids []string, _ time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	set := make(map[string]struct{}, len(ids))
	for _, id := range ids {
		set[id] = struct{}{}
	}
	for i := range s.outbox {
		if _, ok := set[s.outbox[i].event.ID]; ok {
			s.outbox[i].published = true
		}
	}
	return nil
}

// DeleteTenant removes the tenant and everything it owns. It exists for the
// onboarding saga's compensation path; the HTTP API deliberately does not
// expose it.
func (s *Store) DeleteTenant(_ context.Context, id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, ok := s.tenants[id]; !ok {
		return ErrTenantNotFound
	}
	event, err := AuditTenantDeleted(id)
	if err != nil {
		return err
	}
	delete(s.tenants, id)
	for key, p := range s.projects {
		if p.TenantID == id {
			delete(s.projects, key)
		}
	}
	for keyID, rec := range s.apiKeys {
		if rec.key.TenantID == id {
			delete(s.apiKeys, keyID)
		}
	}
	s.appendOutboxLocked(event)
	return nil
}
