package saga

import (
	"context"
	"errors"
	"log/slog"

	"polyforge/internal/classifier"
	"polyforge/internal/controller"
	"polyforge/internal/events"
	"polyforge/internal/tenant"
)

const bootstrapKeyName = "bootstrap"

// OnboardingStore is what tenant onboarding needs from a store: the domain
// writes, the compensating deletes, and standalone outbox appends for the
// event-only steps. The memory and SQLite stores both satisfy it.
type OnboardingStore interface {
	CreateTenant(ctx context.Context, t tenant.Tenant) (tenant.Tenant, error)
	DeleteTenant(ctx context.Context, id string) error
	CreateAPIKey(ctx context.Context, tenantID, name, scope string) (tenant.APIKey, error)
	ListAPIKeys(ctx context.Context, tenantID string) ([]tenant.APIKey, error)
	RevokeAPIKey(ctx context.Context, tenantID, keyID string) error
	AppendEvent(ctx context.Context, e events.Event) error
}

// OnboardingResult carries the artifacts the completed saga produced. The
// bootstrap key secret is only present when this execution minted the key
// (a resumed run that finds the key already minted cannot recover the
// secret — by design, it is never stored).
type OnboardingResult struct {
	State  State
	Tenant tenant.Tenant
	Key    tenant.APIKey
}

// Onboard runs the ADR 0006 onboarding saga: create tenant -> bootstrap
// key -> default policy event -> welcome event. sagaID must be stable per
// logical onboarding (e.g. derived from the request's idempotency key) so a
// crashed run can be resumed with the same ID.
func Onboard(ctx context.Context, store OnboardingStore, states StateStore, sagaID string, t tenant.Tenant, log *slog.Logger) (OnboardingResult, error) {
	t = tenant.NormalizeTenant(t)
	result := OnboardingResult{}

	steps := []Step{
		{
			Name: "create-tenant",
			Run: func(ctx context.Context) error {
				created, err := store.CreateTenant(ctx, t)
				if errors.Is(err, tenant.ErrConflict) {
					// A resumed run finds its own earlier write; treat the
					// existing row as this step's outcome.
					result.Tenant = t
					return nil
				}
				if err != nil {
					return err
				}
				result.Tenant = created
				return nil
			},
			Compensate: func(ctx context.Context) error {
				err := store.DeleteTenant(ctx, t.ID)
				if errors.Is(err, tenant.ErrTenantNotFound) {
					return nil
				}
				return err
			},
		},
		{
			Name: "bootstrap-key",
			Run: func(ctx context.Context) error {
				existing, err := store.ListAPIKeys(ctx, t.ID)
				if err != nil {
					return err
				}
				for _, key := range existing {
					if key.Name == bootstrapKeyName && key.RevokedAt == nil {
						result.Key = key
						return nil
					}
				}
				key, err := store.CreateAPIKey(ctx, t.ID, bootstrapKeyName, tenant.ScopeFull)
				if err != nil {
					return err
				}
				result.Key = key
				return nil
			},
			Compensate: func(ctx context.Context) error {
				keys, err := store.ListAPIKeys(ctx, t.ID)
				if errors.Is(err, tenant.ErrTenantNotFound) {
					return nil
				}
				if err != nil {
					return err
				}
				for _, key := range keys {
					if key.Name == bootstrapKeyName && key.RevokedAt == nil {
						if err := store.RevokeAPIKey(ctx, t.ID, key.ID); err != nil {
							return err
						}
					}
				}
				return nil
			},
		},
		{
			// The platform derives live policy from telemetry; what the saga
			// records is the *fact* that this tenant started from the
			// conservative default, as an event the projection can read.
			Name: "default-policy",
			Run: func(ctx context.Context) error {
				policy := controller.Recommend(classifier.Result{TenantID: t.ID})
				event, err := events.NewWithID("saga_"+sagaID+"_policy", t.ID, events.ActionPolicyDefaulted, policy)
				if err != nil {
					return err
				}
				// Deterministic ID: a crash-retry appends the same row, and
				// both the outbox and JetStream deduplicate it.
				return store.AppendEvent(ctx, event)
			},
			Compensate: compensationEvent(store, sagaID, t.ID, "default-policy"),
		},
		{
			Name: "welcome-event",
			Run: func(ctx context.Context) error {
				event, err := events.NewWithID("saga_"+sagaID+"_welcome", t.ID, events.ActionTenantOnboarded, map[string]string{
					"tenant_id": t.ID,
					"plan":      t.Plan,
				})
				if err != nil {
					return err
				}
				return store.AppendEvent(ctx, event)
			},
			Compensate: compensationEvent(store, sagaID, t.ID, "welcome-event"),
		},
	}

	state, err := New(sagaID, "tenant-onboarding", t.ID, states, steps, log).Execute(ctx)
	result.State = state
	return result, err
}

// compensationEvent makes the undo of an event-only step explicit in the
// stream itself: appended facts cannot be unappended, so the compensating
// action is a compensation event consumers can use to disregard the pair.
func compensationEvent(store OnboardingStore, sagaID, tenantID, step string) func(context.Context) error {
	return func(ctx context.Context) error {
		event, err := events.NewWithID("saga_"+sagaID+"_compensate_"+step, tenantID, events.ActionSagaCompensated, map[string]string{
			"saga_id": sagaID,
			"step":    step,
		})
		if err != nil {
			return err
		}
		return store.AppendEvent(ctx, event)
	}
}
