-- Transactional outbox (ADR 0006). Deliberately no foreign key to tenants:
-- audit events must survive tenant deletion.
CREATE TABLE IF NOT EXISTS outbox (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    published_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbox_unpublished ON outbox(seq) WHERE published_at IS NULL;

-- The app role appends events only for the tenant bound to its transaction;
-- the relay reads and stamps rows through the admin role, which bypasses RLS.
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS outbox_isolation ON outbox;
CREATE POLICY outbox_isolation ON outbox
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

-- Saga state lives in the database, not the broker (ADR 0006). Only the
-- orchestrator (admin role) touches it.
CREATE TABLE IF NOT EXISTS sagas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    steps_done INTEGER NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL
);
