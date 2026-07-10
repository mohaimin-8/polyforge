"""Experiment runner: expand, resume, execute with retry, record (W33/W35a).

Hardening that the smoke week exists to prove:
- resume: run_ids already valid in the DB are skipped, so an interrupted
  matrix continues instead of restarting at run 1;
- retry-on-flake: a failed execution is retried up to `retries` times,
  then recorded as failed with its error — never dropped;
- invalid marking: a run that completes but violates the metric contract
  is recorded `invalid`, and its metrics never enter the analysis tables;
- single writer: workers only compute; every DB write happens on the main
  process, so parallelism cannot corrupt the store.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from . import EVAL_DIR
from .config import ExperimentSpec, RunSpec, expand, load
from .results import check_metrics, connect, record, valid_run_ids, validate

DAILY_LOG = EVAL_DIR / "results" / "DAILY.md"


def execute_backend(run: RunSpec) -> dict:
    if run.backend == "sim":
        from . import sim_backend

        return sim_backend.execute(run)
    if run.backend == "cluster":
        from . import cluster_backend

        return cluster_backend.execute(run)
    raise ValueError(f"unknown backend {run.backend!r}")


def _attempt(run: RunSpec, retries: int) -> tuple[str, int, dict | None, str | None]:
    """Execute with retry. Returns (status, attempts, outcome, error) and
    never raises — the caller records whatever happened."""
    error = None
    for attempt in range(1, retries + 2):
        try:
            outcome = execute_backend(run)
        except Exception:
            error = traceback.format_exc(limit=8)
            continue
        reason = check_metrics(outcome["metrics"], run.steps)
        if reason is None:
            return "valid", attempt, outcome, None
        error = reason  # completed but broke the contract: retry, then mark
    status = "invalid" if error and "\n" not in error else "failed"
    return status, retries + 1, None, error


def _log_progress(experiment: str, done: int, total: int, failed: int, started: float) -> None:
    elapsed = time.time() - started
    rate = done / elapsed * 3600.0 if elapsed > 0 else 0.0
    line = (
        f"| {datetime.now(timezone.utc):%Y-%m-%d %H:%M} | {experiment} "
        f"| {done}/{total} | {failed} | {rate:.0f} runs/h |"
    )
    DAILY_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not DAILY_LOG.exists():
        DAILY_LOG.write_text(
            "# Experiment burn log (W35b)\n\n"
            "Appended by the runner every progress tick.\n\n"
            "| when (UTC) | experiment | done/total | not-valid | burn rate |\n"
            "|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    with open(DAILY_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_experiment(spec: ExperimentSpec, workers: int = 1, limit: int | None = None,
                   progress_every: int = 25) -> dict:
    all_runs = expand(spec)
    db_path = (EVAL_DIR.parent / spec.output).resolve()
    con = connect(db_path)

    done_ids = valid_run_ids(con, spec.name)
    pending = [r for r in all_runs if r.run_id not in done_ids]
    if limit is not None:
        pending = pending[:limit]

    print(
        f"experiment={spec.name} backend={spec.backend} total={len(all_runs)} "
        f"already-valid={len(done_ids)} pending={len(pending)} db={db_path}"
    )

    started = time.time()
    done = failed = 0

    def _record(run: RunSpec, status: str, attempts: int, outcome, error) -> None:
        nonlocal done, failed
        record(con, run, status, attempts, outcome, error)
        done += 1
        failed += status != "valid"
        if done % progress_every == 0 or done == len(pending):
            _log_progress(spec.name, done, len(pending), failed, started)
            print(
                f"  [{done}/{len(pending)}] not-valid={failed} "
                f"({time.time() - started:.0f}s elapsed)"
            )

    if workers <= 1:
        for run in pending:
            status, attempts, outcome, error = _attempt(run, spec.retries)
            _record(run, status, attempts, outcome, error)
    else:
        # Workers compute; only this process writes. Bounded in-flight set
        # keeps memory flat even with timeseries-bearing outcomes.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            in_flight = {}
            queue = list(reversed(pending))
            while queue or in_flight:
                while queue and len(in_flight) < workers * 2:
                    run = queue.pop()
                    in_flight[pool.submit(_attempt, run, spec.retries)] = run
                finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for fut in finished:
                    run = in_flight.pop(fut)
                    status, attempts, outcome, error = fut.result()
                    _record(run, status, attempts, outcome, error)

    report = validate(con, spec.name, len(all_runs))
    con.close()
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("experiment", help="path to experiment YAML")
    ap.add_argument("--dry-run", action="store_true", help="print the matrix and exit")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="run at most N pending runs")
    args = ap.parse_args()

    spec = load(args.experiment)
    if args.dry_run:
        runs = expand(spec)
        print(f"experiment={spec.name} backend={spec.backend} runs={len(runs)}")
        for r in runs[:10]:
            print(f"  {r.run_id}  {r.system:18s} {r.workload:16s} {r.tenant_mix:18s} "
                  f"{r.cluster_size:8s} rep={r.rep} seed={r.seed}")
        if len(runs) > 10:
            print(f"  ... and {len(runs) - 10} more")
        return

    report = run_experiment(spec, workers=args.workers, limit=args.limit)
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
