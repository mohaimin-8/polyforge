package events

import (
	"encoding/json"
	"sort"
	"sync"
	"time"
)

// TenantSummary is the CQRS read model: per-tenant counters derived purely
// from the audit stream. It contains nothing that cannot be recomputed by
// replaying POLYFORGE_AUDIT from sequence 1, which is what the regeneration
// test asserts.
type TenantSummary struct {
	TenantID      string    `json:"tenant_id"`
	Projects      int       `json:"projects"`
	ActiveAPIKeys int       `json:"active_api_keys"`
	Events        int       `json:"events"`
	Deleted       bool      `json:"deleted"`
	LastEventAt   time.Time `json:"last_event_at"`
}

// Projection folds audit events into tenant summaries. Apply is safe for
// concurrent use; the relay-fed live instance and a replay-fed rebuild must
// serialize to identical JSON.
type Projection struct {
	mu        sync.Mutex
	summaries map[string]*TenantSummary
}

func NewProjection() *Projection {
	return &Projection{summaries: map[string]*TenantSummary{}}
}

func (p *Projection) Apply(e Event) {
	p.mu.Lock()
	defer p.mu.Unlock()

	s := p.summaries[e.TenantID]
	if s == nil {
		s = &TenantSummary{TenantID: e.TenantID}
		p.summaries[e.TenantID] = s
	}
	s.Events++
	if e.OccurredAt.After(s.LastEventAt) {
		s.LastEventAt = e.OccurredAt
	}
	switch e.Action {
	case ActionProjectCreated:
		s.Projects++
	case ActionProjectDeleted:
		s.Projects--
	case ActionAPIKeyCreated, ActionTenantProvisioned:
		// Provisioning mints the bootstrap key in the same transaction.
		s.ActiveAPIKeys++
	case ActionAPIKeyRevoked:
		s.ActiveAPIKeys--
	case ActionAPIKeyRotated:
		// Rotation mints one key and revokes one: net zero.
	case ActionTenantDeleted:
		s.Deleted = true
		s.Projects = 0
		s.ActiveAPIKeys = 0
	}
}

// Summary returns a copy of one tenant's summary.
func (p *Projection) Summary(tenantID string) (TenantSummary, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	s, ok := p.summaries[tenantID]
	if !ok {
		return TenantSummary{}, false
	}
	return *s, true
}

// JSON serializes every summary sorted by tenant ID. Byte equality of two
// projections' JSON is the rebuild test's success criterion.
func (p *Projection) JSON() ([]byte, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := make([]TenantSummary, 0, len(p.summaries))
	for _, s := range p.summaries {
		out = append(out, *s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].TenantID < out[j].TenantID })
	return json.Marshal(out)
}
