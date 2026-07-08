-- API-key scopes: read (read-only endpoints) or full (read + mutate).
-- Keys issued before scopes existed keep their original full authority,
-- so the column defaults to 'full' rather than the least privilege.
ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'full'
    CHECK (scope IN ('read', 'full'));
