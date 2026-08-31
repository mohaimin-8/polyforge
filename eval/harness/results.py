"""Result store: one DuckDB file per experiment, standardized schema (W33).

Three tables:
- runs        — one row per attempted run: identity, status, provenance
- metrics     — one row per *valid* run: the aggregates the stats use
- timeseries  — per-step, per-tenant rows for runs that keep them

`status` is the integrity contract: `valid` rows have complete finite
metrics; anything else (`failed`, `invalid`) keeps its error string and is
excluded by every downstream query. A crashed run is recorded as failed,
never silently dropped — W33's "marked invalid, not silently corrupted".
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import HARNESS_VERSION
from .config import RunSpec

METRIC_COLUMNS = (
    "total_cost_usd",
    "mean_violation",
    "violation_step_share",
    "mean_jain",
    "cache_hit_rate",
    "crud_p95_ms",
    "ai_p95_ms",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    experiment      TEXT NOT NULL,
    backend         TEXT NOT NULL,
    system          TEXT NOT NULL,
    workload        TEXT NOT NULL,
    tenant_mix      TEXT NOT NULL,
    cluster_size    TEXT NOT NULL,
    rep             INTEGER NOT NULL,
    seed            BIGINT NOT NULL,
    steps           INTEGER NOT NULL,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL,
    wall_s          DOUBLE,
    tenants         INTEGER,
    error           TEXT,
    harness_version TEXT NOT NULL,
    recorded_at     TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
    run_id               TEXT PRIMARY KEY,
    total_cost_usd       DOUBLE NOT NULL,
    mean_violation       DOUBLE NOT NULL,
    violation_step_share DOUBLE NOT NULL,
    mean_jain            DOUBLE NOT NULL,
    cache_hit_rate       DOUBLE NOT NULL,
    crud_p95_ms          DOUBLE NOT NULL,
    ai_p95_ms            DOUBLE NOT NULL,
    crud_p99_ms          DOUBLE,
    ai_p99_ms            DOUBLE,
    steps                INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS timeseries (
    run_id         TEXT NOT NULL,
    step           INTEGER NOT NULL,
    tenant         TEXT NOT NULL,
    replicas       INTEGER NOT NULL,
    cache_mb       INTEGER NOT NULL,
    tier           TEXT NOT NULL,
    cost_usd       DOUBLE NOT NULL,
    violation      DOUBLE NOT NULL,
    crud_p95_ms    DOUBLE NOT NULL,
    ai_p95_ms      DOUBLE NOT NULL,
    cache_hit_rate DOUBLE NOT NULL
);
"""


def connect(path: str | Path) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(_SCHEMA)
    # p99 is a live-only column added in Wave 3 (PREREG_LIVE_CHAOS_P99.md);
    # migrate older dbs in place so a fresh schema and an existing one agree.
    con.execute("ALTER TABLE metrics ADD COLUMN IF NOT EXISTS crud_p99_ms DOUBLE")
    con.execute("ALTER TABLE metrics ADD COLUMN IF NOT EXISTS ai_p99_ms DOUBLE")
    # PREREG_EVICTION_PARITY reporting pair. Nullable and outside
    # METRIC_COLUMNS on purpose: every committed DB predates them, and a
    # required column would retroactively invalidate its rows.
    con.execute("ALTER TABLE metrics ADD COLUMN IF NOT EXISTS mean_excess DOUBLE")
    con.execute("ALTER TABLE metrics ADD COLUMN IF NOT EXISTS tier_none_step_share DOUBLE")
    return con


def valid_run_ids(con: duckdb.DuckDBPyConnection, experiment: str,
                  backend: str | None = None) -> set[str]:
    """Run ids already completed for this experiment, for resume.

    `backend` must be passed by the runner. run_identity does not hash the
    backend — adding it would churn every committed run_id — so without this
    filter, pointing a `backend: cluster` spec at a DB that already holds the
    sim rows for the same experiment name made every id look done: `pending`
    came out empty, validate() reported ok, and the runner announced success
    having executed nothing, with a table of sim rows under a cluster
    experiment's name.
    """
    if backend is None:
        rows = con.execute(
            "SELECT run_id FROM runs WHERE experiment = ? AND status = 'valid'",
            [experiment]).fetchall()
    else:
        rows = con.execute(
            "SELECT run_id FROM runs WHERE experiment = ? AND status = 'valid' "
            "AND backend = ?", [experiment, backend]).fetchall()
    return {r[0] for r in rows}


def check_metrics(metrics: dict, expected_steps: int,
                  expects_ai: bool = False,
                  expects_crud: bool = True) -> str | None:
    """Sanity contract for a run's aggregates; a violation makes the run
    `invalid` (recorded, excluded, retried). Returns the reason or None.

    Presence, not just finiteness. The finiteness checks below cannot tell a
    measured zero from a measurement that never happened: a live campaign
    once recorded latencies of exactly 0.0 for every tenant because telemetry
    went to a per-replica database instead of a shared one, and those rows
    passed this function, were marked `valid`, and were averaged into a
    committed record. `n_events` is the direct witness — the Go exporter has
    always emitted it and the harness used to discard it — and a served
    request cannot have a p95 of zero.
    """
    for name in METRIC_COLUMNS:
        v = metrics.get(name)
        if v is None or not math.isfinite(v):
            return f"metric {name} is missing or non-finite: {v!r}"
    if metrics["steps"] != expected_steps:
        return f"steps {metrics['steps']} != expected {expected_steps}"
    if not (0.0 <= metrics["mean_jain"] <= 1.0):
        return f"jain {metrics['mean_jain']} outside [0,1]"
    if metrics["total_cost_usd"] <= 0.0:
        return f"non-positive cost {metrics['total_cost_usd']}"

    # A run that observed no telemetry measured nothing, whatever its cost
    # says: infra cost is derived from the replica count and is non-zero
    # whenever pods merely exist.
    n_events = metrics.get("n_events")
    if n_events is not None and n_events <= 0:
        return "no telemetry events observed: the run measured nothing"
    # `expects_crud` mirrors `expects_ai`. It defaults True because every
    # synthetic workload class carries CRUD, so an unconditional check was
    # correct until L5: a trace-driven cell replays BurstGPT, which records LLM
    # arrivals and nothing else, so its demand is chat-only and a zero
    # crud_p95 is the CORRECT reading rather than a dead exporter. Guarding it
    # unconditionally would have failed every trace-driven run for being what
    # its pre-registration says it is.
    if expects_crud and metrics["crud_p95_ms"] <= 0.0:
        return (f"crud_p95_ms is {metrics['crud_p95_ms']} — a served request "
                "cannot have a zero p95; telemetry did not reach the exporter")
    # An AI-only run still has to have measured SOMETHING, or the guard above
    # has merely been switched off: with no CRUD expected, the AI path becomes
    # the one that must be non-zero.
    if not expects_crud and not expects_ai:
        return ("neither CRUD nor AI telemetry is expected; this run could not "
                "have measured anything")
    if expects_ai and metrics["ai_p95_ms"] <= 0.0:
        return (f"ai_p95_ms is {metrics['ai_p95_ms']} on an AI-bearing "
                "workload; the AI path produced no telemetry")
    return None


def record(con: duckdb.DuckDBPyConnection, run: RunSpec, status: str, attempts: int,
           outcome: dict | None = None, error: str | None = None) -> None:
    """Upsert one run's row(s) atomically. Replays overwrite their own
    run_id; they can never touch another run's rows."""
    outcome = outcome or {}
    metrics = outcome.get("metrics")
    con.execute("BEGIN")
    try:
        con.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                run.run_id, run.experiment, run.backend, run.system, run.workload,
                run.tenant_mix, run.cluster_size, run.rep, run.seed, run.steps,
                status, attempts, outcome.get("wall_s"), outcome.get("tenants"),
                error, HARNESS_VERSION, datetime.now(timezone.utc),
            ],
        )
        con.execute("DELETE FROM metrics WHERE run_id = ?", [run.run_id])
        con.execute("DELETE FROM timeseries WHERE run_id = ?", [run.run_id])
        if status == "valid" and metrics:
            # p99 is live-only (cluster backend); sim runs leave it NULL. Named
            # columns so the nullable p99 pair rides beside the required p95 set.
            con.execute(
                "INSERT INTO metrics "
                "(run_id, total_cost_usd, mean_violation, violation_step_share, "
                "mean_jain, cache_hit_rate, crud_p95_ms, ai_p95_ms, "
                "crud_p99_ms, ai_p99_ms, mean_excess, tier_none_step_share, steps) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [run.run_id] + [metrics[c] for c in METRIC_COLUMNS]
                + [metrics.get("crud_p99_ms"), metrics.get("ai_p99_ms"),
                   metrics.get("mean_excess"), metrics.get("tier_none_step_share"),
                   metrics["steps"]],
            )
            rows = outcome.get("timeseries") or []
            if rows:
                con.executemany(
                    "INSERT INTO timeseries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        [
                            run.run_id, r["step"], r["tenant"], r["replicas"], r["cache_mb"],
                            r["tier"], r["cost_usd"], r["violation"], r["crud_p95_ms"],
                            r["ai_p95_ms"], r["cache_hit_rate"],
                        ]
                        for r in rows
                    ],
                )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def validate(con: duckdb.DuckDBPyConnection, experiment: str, expected_runs: int) -> dict:
    """The W35c integrity report: exact row count, no duplicates, no NULLs,
    every valid run has metrics."""
    q = lambda sql, *p: con.execute(sql, list(p)).fetchone()[0]  # noqa: E731
    report = {
        "experiment": experiment,
        "expected_runs": expected_runs,
        "valid_runs": q(
            "SELECT count(*) FROM runs WHERE experiment=? AND status='valid'", experiment
        ),
        "failed_runs": q(
            "SELECT count(*) FROM runs WHERE experiment=? AND status<>'valid'", experiment
        ),
        "duplicate_run_ids": q(
            "SELECT count(*) FROM (SELECT run_id FROM runs GROUP BY run_id HAVING count(*)>1)"
        ),
        "orphan_metrics": q(
            "SELECT count(*) FROM metrics m LEFT JOIN runs r USING (run_id) "
            "WHERE r.run_id IS NULL"
        ),
        "valid_without_metrics": q(
            "SELECT count(*) FROM runs r LEFT JOIN metrics m USING (run_id) "
            "WHERE r.experiment=? AND r.status='valid' AND m.run_id IS NULL", experiment
        ),
        # More than one substrate under one experiment name means sim and
        # live rows are pooled in a table the analysis reads as homogeneous.
        # They are not comparable in absolute terms — the whole phase7
        # protocol exists because they are only ordinally comparable.
        "distinct_backends": q(
            "SELECT count(DISTINCT backend) FROM runs WHERE experiment=?", experiment
        ),
        # Likewise a mixture of harness versions: results produced by
        # different code, indistinguishable once pooled.
        "distinct_harness_versions": q(
            "SELECT count(DISTINCT harness_version) FROM runs WHERE experiment=?",
            experiment
        ),
        "null_metrics": q(
            "SELECT count(*) FROM metrics WHERE "
            + " OR ".join(f"{c} IS NULL" for c in METRIC_COLUMNS)
        ),
    }
    report["ok"] = (
        report["valid_runs"] == expected_runs
        and report["duplicate_run_ids"] == 0
        and report["orphan_metrics"] == 0
        and report["valid_without_metrics"] == 0
        and report["null_metrics"] == 0
        and report["distinct_backends"] <= 1
        and report["distinct_harness_versions"] <= 1
    )
    return report
