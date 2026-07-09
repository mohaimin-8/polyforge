// Package events implements the ADR 0006 event backbone: a transactional
// outbox written by the stores, a relay that publishes outbox rows to NATS
// JetStream exactly-once-in-effect, and a CQRS projection rebuilt from
// stream replay.
package events

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// Stream and subject layout, fixed by ADR 0006. polyforge.events.> stays
// reserved for the JCAC control loop.
const (
	StreamName    = "POLYFORGE_AUDIT"
	SubjectRoot   = "polyforge.audit"
	SubjectFilter = SubjectRoot + ".>"
)

// Audit actions emitted by the stores. The action is the last subject token,
// so consumers can filter e.g. polyforge.audit.*.project:created.
const (
	ActionTenantProvisioned = "tenant.provisioned"
	ActionTenantCreated     = "tenant.created"
	ActionTenantDeleted     = "tenant.deleted"
	ActionTenantOnboarded   = "tenant.onboarded"
	ActionPolicyDefaulted   = "policy.defaulted"
	ActionProjectCreated    = "project.created"
	ActionProjectUpdated    = "project.updated"
	ActionProjectDeleted    = "project.deleted"
	ActionAPIKeyCreated     = "apikey.created"
	ActionAPIKeyRevoked     = "apikey.revoked"
	ActionAPIKeyRotated     = "apikey.rotated"
	ActionSagaCompensated   = "saga.compensated"
)

// Event is one audit record. ID doubles as the Nats-Msg-Id header, which is
// what makes the relay's at-least-once delivery effectively once: JetStream
// drops any republish of an ID it has seen inside the duplicate window.
type Event struct {
	ID         string          `json:"id"`
	TenantID   string          `json:"tenant_id"`
	Action     string          `json:"action"`
	Subject    string          `json:"subject"`
	OccurredAt time.Time       `json:"occurred_at"`
	Payload    json.RawMessage `json:"payload,omitempty"`
}

// Subject builds the per-tenant audit subject. NATS subjects use "." as the
// token separator, so the action's own dot becomes ":" to keep the tenant ID
// and action each a single token.
func Subject(tenantID, action string) string {
	return SubjectRoot + "." + tenantID + "." + strings.ReplaceAll(action, ".", ":")
}

// New builds an event with a random ID. Use NewWithID when the caller needs
// a deterministic ID (the saga does, for crash-safe idempotent retries).
func New(tenantID, action string, payload any) (Event, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return Event{}, err
	}
	return NewWithID("evt_"+hex.EncodeToString(raw), tenantID, action, payload)
}

func NewWithID(id, tenantID, action string, payload any) (Event, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return Event{}, fmt.Errorf("marshal %s payload: %w", action, err)
	}
	return Event{
		ID:         id,
		TenantID:   tenantID,
		Action:     action,
		Subject:    Subject(tenantID, action),
		OccurredAt: time.Now().UTC(),
		Payload:    body,
	}, nil
}

// marshalEvent is the wire encoding on the stream; the outbox row and the
// published message carry the same JSON so replay reconstructs the exact
// event the store recorded.
func marshalEvent(e Event) ([]byte, error) {
	return json.Marshal(e)
}

func unmarshalEvent(data []byte) (Event, error) {
	var e Event
	if err := json.Unmarshal(data, &e); err != nil {
		return Event{}, err
	}
	return e, nil
}
