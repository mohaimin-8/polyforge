package tenant

import (
	"context"
	"errors"
	"fmt"
)

// Tenant isolation modes (roadmap W21, ADR 0008). The three models trade
// cost against blast radius:
//
//	pool   — shared schema, PostgreSQL row-level security (ADR 0003)
//	bridge — schema-per-tenant in the shared database
//	silo   — dedicated Kubernetes namespace with ResourceQuota/LimitRange
//	         (deploy/k8s/tenant-silo-template.yaml)
//
// Every tenant starts in pool; the operator promotes upward as the
// tenant's workload grows. Demotion is deliberately unsupported: moving a
// tenant to a weaker isolation level silently changes its security
// posture, so it must be a migration project, not an API call.
const (
	IsolationPool   = "pool"
	IsolationBridge = "bridge"
	IsolationSilo   = "silo"
)

var ErrInvalidPromotion = errors.New("invalid isolation promotion")

// NormalizeIsolationMode maps the empty mode to pool, mirroring
// NormalizeTenant's default.
func NormalizeIsolationMode(mode string) string {
	if mode == "" {
		return IsolationPool
	}
	return mode
}

func ValidIsolationMode(mode string) bool {
	switch NormalizeIsolationMode(mode) {
	case IsolationPool, IsolationBridge, IsolationSilo:
		return true
	default:
		return false
	}
}

func isolationRank(mode string) int {
	switch NormalizeIsolationMode(mode) {
	case IsolationPool:
		return 0
	case IsolationBridge:
		return 1
	case IsolationSilo:
		return 2
	default:
		return -1
	}
}

// ValidatePromotion enforces the upward-only state machine. Skipping a
// level (pool → silo) is allowed; staying put or moving down is not.
func ValidatePromotion(from, to string) error {
	fromRank, toRank := isolationRank(from), isolationRank(to)
	if fromRank < 0 || toRank < 0 {
		return fmt.Errorf("%w: unknown isolation mode", ErrInvalidPromotion)
	}
	if toRank <= fromRank {
		return fmt.Errorf("%w: %s -> %s is not an upward move", ErrInvalidPromotion, NormalizeIsolationMode(from), NormalizeIsolationMode(to))
	}
	return nil
}

// PromoteTenantIsolation moves the tenant up the isolation ladder and
// records the transition in the outbox, so downstream automation (silo
// namespace provisioning, bridge schema migration) is event-driven.
func (s *Store) PromoteTenantIsolation(_ context.Context, tenantID, mode string) (Tenant, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tenants[tenantID]
	if !ok {
		return Tenant{}, ErrTenantNotFound
	}
	if err := ValidatePromotion(t.IsolationMode, mode); err != nil {
		return Tenant{}, err
	}
	from := t.IsolationMode
	t.IsolationMode = NormalizeIsolationMode(mode)
	event, err := AuditTenantIsolationPromoted(t, from)
	if err != nil {
		return Tenant{}, err
	}
	s.tenants[tenantID] = t
	s.appendOutboxLocked(event)
	return t, nil
}
