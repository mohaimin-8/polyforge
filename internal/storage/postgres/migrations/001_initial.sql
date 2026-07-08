CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT NOT NULL,
    isolation_mode TEXT NOT NULL CHECK (isolation_mode IN ('pool', 'bridge', 'silo')),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    service TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    rps_window DOUBLE PRECISION NOT NULL CHECK (rps_window >= 0),
    payload_bytes BIGINT NOT NULL CHECK (payload_bytes >= 0),
    latency_ms DOUBLE PRECISION NOT NULL CHECK (latency_ms >= 0),
    cache_hit BOOLEAN NOT NULL,
    embedding_density DOUBLE PRECISION NOT NULL CHECK (embedding_density BETWEEN 0 AND 1),
    model_tier TEXT NOT NULL,
    child_spans INTEGER NOT NULL CHECK (child_spans >= 0)
);

CREATE INDEX IF NOT EXISTS idx_projects_tenant_id ON projects(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_created ON api_keys(tenant_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_telemetry_tenant_time ON telemetry_events(tenant_id, timestamp DESC);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects FORCE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE telemetry_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE telemetry_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON tenants;
CREATE POLICY tenant_isolation ON tenants
    USING (id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), ''));

DROP POLICY IF EXISTS project_isolation ON projects;
CREATE POLICY project_isolation ON projects
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

DROP POLICY IF EXISTS api_key_isolation ON api_keys;
CREATE POLICY api_key_isolation ON api_keys
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

DROP POLICY IF EXISTS telemetry_isolation ON telemetry_events;
CREATE POLICY telemetry_isolation ON telemetry_events
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));
