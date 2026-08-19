#!/usr/bin/env bash
# Time-resolved extraction for the WP14 live soak — the data `eval-export`
# does not emit.
#
# WHY THIS EXISTS. PREREG_LIVE_SOAK_V2 freezes SK-H1 on post-fault
# `mean_violation` recovering within CHAOS_TOL of the pre-fault level, and
# SK-H3 on *hour-bucketed* crud_p95. Neither is computable from the harness's
# output: `control-plane eval-export` emits scalar aggregates over the whole
# run and no timeseries at all (cmd/control-plane/evalexport.go — there is no
# timeseries field, so cluster_backend's `export.get("timeseries", [])` is
# always empty), and live_soak.yaml sets `timeseries_reps: 0`, which makes
# `store_timeseries` False, so the DuckDB timeseries table stays empty too.
#
# The per-event data does exist, in the shared Postgres `telemetry_events`
# table. This extracts hour buckets and 10 s control steps from it, so the
# frozen hypotheses can be scored as written rather than quietly restated.
#
# IT MUST RUN BEFORE TEARDOWN. cluster_backend's step list ends
# `eval-export` -> `kind delete cluster`, and the `finally` block deletes the
# cluster again on every exit path. Once that runs, Postgres and all 16.9M
# events are gone. There is no later opportunity.
#
# METHOD MIRRORS THE EXPORTER EXACTLY, so the buckets are comparable with the
# committed scalars rather than merely similar:
#   * CRUD = events with empty model_tier   (evalexport.go: isAI := ModelTier != "")
#   * target = 150.0 ms * plan scale        (evalSLOBaseMS x evalSLOClassScale)
#   * percentile = nearest-rank ceil(q*n)-1 (evalPercentile) == percentile_disc,
#     NOT percentile_cont, which interpolates and disagrees in the 4th decimal
#   * step = unix_seconds / 10              (evalStepSeconds)
#
# Read-only: SELECTs only. Safe to run while the soak is live, though doing so
# adds a scan the measured window would not otherwise carry -- disclose it.

set -euo pipefail
NS=polyforge
EV="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/results/live_soak_evidence"
TAG=${TAG:-final}

PG=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null |
     awk '/^postgres/ {print $1; exit}')
[ -n "$PG" ] || { echo "no postgres pod; cluster already gone?" >&2; exit 1; }

q() { kubectl exec -n "$NS" "$PG" -- psql -U postgres -d polyforge -q -A -F, -c "$1"; }

# Plan scales come from the tenants table, not an assumption, so a non-uniform
# mix produces per-tenant targets instead of one wrong global one.
echo "[soak-export] tenant plans:"
q "SELECT plan, count(*) FROM tenants GROUP BY 1 ORDER BY 1;" | sed 's/^/  /'

TARGET="150.0 * CASE t.plan WHEN 'premium' THEN 1.0 WHEN 'standard' THEN 2.5 WHEN 'best-effort' THEN 8.0 END"
CRUD="trim(coalesce(e.model_tier,'')) = ''"

echo "[soak-export] hourly buckets -> soak_hourly_${TAG}.csv"
q "SELECT date_trunc('hour', e.timestamp) AS hour_utc,
          count(*) AS n_events,
          percentile_disc(0.95) WITHIN GROUP (ORDER BY e.latency_ms) AS crud_p95_ms,
          percentile_disc(0.99) WITHIN GROUP (ORDER BY e.latency_ms) AS crud_p99_ms,
          sum(CASE WHEN e.latency_ms > ($TARGET) THEN 1 ELSE 0 END) AS over_target,
          avg(CASE WHEN e.latency_ms > ($TARGET) THEN 1.0 ELSE 0.0 END) AS mean_violation
     FROM telemetry_events e JOIN tenants t ON t.id = e.tenant_id
    WHERE $CRUD
    GROUP BY 1 ORDER BY 1;" > "$EV/soak_hourly_${TAG}.csv"
echo "  $(( $(wc -l < "$EV/soak_hourly_${TAG}.csv") - 1 )) hour buckets"

echo "[soak-export] 10 s control steps -> soak_steps_${TAG}.csv"
q "SELECT floor(extract(epoch FROM e.timestamp) / 10)::bigint AS step,
          min(e.timestamp) AS step_start_utc,
          count(*) AS n_events,
          sum(CASE WHEN e.latency_ms > ($TARGET) THEN 1 ELSE 0 END) AS over_target,
          avg(CASE WHEN e.latency_ms > ($TARGET) THEN 1.0 ELSE 0.0 END) AS violation
     FROM telemetry_events e JOIN tenants t ON t.id = e.tenant_id
    WHERE $CRUD
    GROUP BY 1 ORDER BY 1;" > "$EV/soak_steps_${TAG}.csv"
echo "  $(( $(wc -l < "$EV/soak_steps_${TAG}.csv") - 1 )) steps"

echo "[soak-export] run-level scalars -> soak_scalars_${TAG}.csv"
q "SELECT count(*) AS n_events,
          percentile_disc(0.95) WITHIN GROUP (ORDER BY e.latency_ms) AS crud_p95_ms,
          percentile_disc(0.99) WITHIN GROUP (ORDER BY e.latency_ms) AS crud_p99_ms,
          avg(CASE WHEN e.latency_ms > ($TARGET) THEN 1.0 ELSE 0.0 END) AS mean_violation,
          min(e.timestamp) AS first_event_utc,
          max(e.timestamp) AS last_event_utc
     FROM telemetry_events e JOIN tenants t ON t.id = e.tenant_id
    WHERE $CRUD;" > "$EV/soak_scalars_${TAG}.csv"
cat "$EV/soak_scalars_${TAG}.csv" | sed 's/^/  /'
echo "[soak-export] done"
