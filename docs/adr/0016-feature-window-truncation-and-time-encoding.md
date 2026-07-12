# ADR 0016: Feature-window truncation is disclosed, not silent; time encoding is fixed-width

Status: accepted · 2026-07-12 · implemented with tests across all three stores

## Context

`Features` reads at most `MaxFeatureEvents` (10,000) events per query
(`ORDER BY timestamp LIMIT`). A tenant above that rate in the requested
window therefore gets aggregates over the *earliest 10,000 events* — a
prefix of the window, not the window. The session log flagged this as an
evaluation-validity risk: `p95_latency_ms` over a truncated window is a
different measurement than the response claims, and nothing in the
response said truncation happened. The cap itself is correct (it bounds
memory and latency on the hot analytical path, and ClickHouse is the
designated home for unbounded scans per ADR 0011); the silence was the bug.

While testing the fix with sub-second timestamps, a second latent defect
surfaced in the SQLite store: `encodeTime` used `time.RFC3339Nano`, which
trims trailing zeros. Time columns are TEXT and every window bound and
`ORDER BY` compares them lexicographically — and trimmed fractions do not
sort chronologically (`"…43.001Z" < "…43Z"` as strings). A `since` bound
at a whole second silently excluded same-second fractional events. No
existing test used sub-second timestamps, so the suite never caught it.

## Decision

**Truncation is reported, never repaired.** `FeatureSet` gains a required
`truncated` boolean, exposed verbatim over HTTP and documented in the
OpenAPI schema. The server does not narrow the window, raise the cap, or
error: the caller asked a valid question and gets the honest answer plus
the fact that it covers a prefix. Choosing a narrower window or a service
filter is the caller's decision, mirroring ADR-style "400, not repair"
reasoning at the query layer — a malformed request fails loudly, a valid
but oversized one discloses loudly.

**Detection is exact, not inferred.** Each SQL store selects
`MaxFeatureEvents + 1` rows; the overflow row proves truncation and is
discarded before aggregation. Reading exactly 10,000 events therefore
never false-positives. The clamp lives in one place
(`telemetry.ClampFeatureEvents`) for the same reason `BuildFeatureSet`
does: three stores must not drift on which prefix of the window survives.
The in-memory store now also selects in timestamp order before clamping,
matching the SQL stores' `ORDER BY timestamp` semantics exactly.

**Time encoding is fixed-width.** `encodeTime` emits a constant 9-digit
fraction (`2006-01-02T15:04:05.000000000Z`), making lexicographic TEXT
order equal chronological order for every window bound and `ORDER BY`.
`migrate()` pads rows written by older builds in place (idempotent,
guarded by string length), so mixed-format databases cannot mis-window at
second boundaries. PostgreSQL is unaffected (`TIMESTAMPTZ` compares
natively).

## Consequences

- Any consumer that polls features for control decisions can now detect
  when its percentile is a prefix measurement and react (narrow the
  window) instead of silently consuming a mislabeled number.
- The OpenAPI schema marks `truncated` required, so generated clients
  fail loudly if the field disappears — the disclosure cannot regress
  silently.
- Legacy SQLite databases are rewritten once at open; the migration is a
  no-op on already-normalized rows.
- The evaluation harness itself is unaffected (the research matrix runs
  on the simulator, not this store), but the live-path claim "features
  are computed over the stated window" is now either true or disclosed
  as false per response.
