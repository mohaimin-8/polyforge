package tenant

import "polyforge/internal/events"

// Audit event constructors shared by the memory, SQLite, and PostgreSQL
// stores, so every backend writes byte-identical payloads into its outbox.
// Payloads never include key secrets or hashes: the stream outlives key
// rotation and is readable by consumers that must never see credentials.

type auditKey struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Scope  string `json:"scope"`
	Prefix string `json:"prefix"`
}

func auditKeyOf(key APIKey) auditKey {
	return auditKey{ID: key.ID, Name: key.Name, Scope: NormalizeScope(key.Scope), Prefix: key.Prefix}
}

func AuditTenantProvisioned(t Tenant, key APIKey) (events.Event, error) {
	return events.New(t.ID, events.ActionTenantProvisioned, map[string]any{
		"tenant": t, "bootstrap_key": auditKeyOf(key),
	})
}

func AuditTenantCreated(t Tenant) (events.Event, error) {
	return events.New(t.ID, events.ActionTenantCreated, t)
}

func AuditTenantDeleted(tenantID string) (events.Event, error) {
	return events.New(tenantID, events.ActionTenantDeleted, map[string]string{"id": tenantID})
}

func AuditProjectCreated(p Project) (events.Event, error) {
	return events.New(p.TenantID, events.ActionProjectCreated, p)
}

func AuditProjectUpdated(p Project) (events.Event, error) {
	return events.New(p.TenantID, events.ActionProjectUpdated, p)
}

func AuditProjectDeleted(tenantID, projectID string) (events.Event, error) {
	return events.New(tenantID, events.ActionProjectDeleted, map[string]string{"id": projectID})
}

func AuditAPIKeyCreated(key APIKey) (events.Event, error) {
	return events.New(key.TenantID, events.ActionAPIKeyCreated, auditKeyOf(key))
}

func AuditAPIKeyRevoked(tenantID, keyID string) (events.Event, error) {
	return events.New(tenantID, events.ActionAPIKeyRevoked, map[string]string{"id": keyID})
}

func AuditAPIKeyRotated(newKey APIKey, revokedKeyID string) (events.Event, error) {
	return events.New(newKey.TenantID, events.ActionAPIKeyRotated, map[string]any{
		"new_key": auditKeyOf(newKey), "revoked_key_id": revokedKeyID,
	})
}
