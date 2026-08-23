#!/usr/bin/env python3
"""Score WP14 Stage B's positive controls against the recorded buckets.

Stage B exists to prove each hypothesis CAN FAIL. The injector script asserts
what it can see live (fallback lines, throttle applied); this scores what only
the recorded data can answer, and it is deliberately a separate program from
the one doing the injecting — a fault injector that also grades its own effect
is not evidence.

Two verdicts:

  SK-H1 latency control — the fault window must push crud_p95 more than 20%
  above the pre-fault baseline. SK-H1 was re-registered on p95 deviation
  precisely because `mean_violation` is pinned at zero by a 375 ms target
  against an ~8 ms p99, so it could not fail. If p95 does not move here, the
  replacement metric is no better than the one it replaced and Phase 3 is not
  done.

  The fault is CPU starvation, not `tc netem`. crud_p95_ms comes from the
  `latency_ms` in internal/platform/replay.go, which times burnCPU and nothing
  else -- network delay lands outside that window and cannot move it, so netem
  would have produced "does not move" and been misread as the metric still
  being vacuous. Starvation lands inside it, because burnCPU spins against a
  wall-clock deadline.

  SK-H2 audit continuity — degraded cycles must equal audit records, with both
  greater than zero. For four attempts this computed 0 == 0 and returned true.

    python scripts/analyse_stage_b.py
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "eval" / "results" / "soak_stage_b_evidence"
DEVIATION_BAND = 0.20


def load_timeline() -> list[tuple[int, str]]:
    p = EVIDENCE / "timeline.txt"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            out.append((int(parts[0]), parts[1]))
    return out


def load_buckets() -> list[dict]:
    """Prefer the fine-grained export.

    The sitting exports twice: coarse (hour) buckets for SK-H3 and fine (60 s)
    buckets for SK-H1, because a five-minute recovery cannot be resolved at
    hour width and percentiles do not aggregate. SK-H1 is scored here, so the
    fine file is the right input whenever it exists; a stage that ran only one
    export still works through the fallback.
    """
    for name in ("eval-export-fine.json", "eval-export.json"):
        p = EVIDENCE / name
        if p.exists():
            buckets = json.loads(p.read_text(encoding="utf-8")).get("buckets", [])
            if buckets:
                print(f"  (buckets read from {name})")
                return buckets
    return []


def bucket_epoch(row: dict) -> int:
    return int(dt.datetime.fromisoformat(
        row["bucket_start_utc"].replace("Z", "+00:00")).timestamp())


def score_latency(buckets: list[dict], timeline: list[tuple[int, str]]) -> bool:
    on = next((t for t, ev in timeline if ev in ("cpu_on", "netem_on")), None)
    off = next((t for t, ev in timeline if ev in ("cpu_off", "netem_off")), None)
    if on is None or off is None:
        print("  latency-fault window not recorded in timeline.txt — cannot score")
        return False

    during = [b for b in buckets if on - 60 <= bucket_epoch(b) <= off]
    before = [b for b in buckets if bucket_epoch(b) < on - 60]
    if not during or not before:
        print(f"  insufficient buckets (before={len(before)} during={len(during)})")
        return False

    # Baseline is the median of the pre-fault buckets rather than the mean, so
    # one noisy minute cannot set the bar it has to clear.
    base_series = sorted(b["crud_p95_ms"] for b in before)
    baseline = base_series[len(base_series) // 2]
    peak = max(b["crud_p95_ms"] for b in during)
    deviation = (peak - baseline) / baseline if baseline else 0.0

    print(f"  pre-fault median p95 : {baseline:.3f} ms  ({len(before)} buckets)")
    print(f"  peak p95 under fault : {peak:.3f} ms  ({len(during)} buckets)")
    print(f"  deviation            : {deviation:+.1%}  (band +/-{DEVIATION_BAND:.0%})")
    failed_on_demand = deviation > DEVIATION_BAND
    print(f"  SK-H1 under fault    : {'FAILS (correct)' if failed_on_demand else 'does not move'}")
    return failed_on_demand


def score_audit() -> bool:
    """Degraded cycles vs the audit records they must have produced.

    The pre-registered rule is "degraded-cycle count == audit-record count,
    both > 0". As scored through attempt 4 that compared 14 against 2,888 and
    called it a pass, because the two sides were never the same quantity: the
    operator audits EVERY cycle for EVERY tenant, so a 60-minute run puts
    8 x 360 = 2,880 records on the stream whether or not anything degraded.
    A rule whose two sides differ by two orders of magnitude under healthy
    operation is not a continuity test, it is an inequality that happens to
    hold.

    What is actually claimed is that no degraded cycle goes unrecorded. During
    a planner outage every cycle falls back, so the stream's growth ACROSS the
    outage window is exactly the fallback records, and the expected value is
    (degraded cycles x tenants). That is a comparison of like with like, and it
    fails if records are dropped.

    The window is closed after a settle period, so a few post-recovery cycles
    can land inside it; three cycles of slack is allowed above the expectation
    and none below. Missing records are the failure this exists to catch.
    """
    controls = EVIDENCE / "positive_controls.log"
    if not controls.exists():
        print("  positive_controls.log missing")
        return False
    windows = EVIDENCE / "audit_window.csv"
    if not windows.exists():
        print("  audit_window.csv missing — the run predates per-injection audit")
        print("  SK-H2: NOT SCOREABLE (this is a fail, not a pass)")
        return False

    rows = list(csv.DictReader(windows.read_text(encoding="utf-8").splitlines()))
    if not rows:
        print("  audit_window.csv is empty — no injection recorded a window")
        return False

    ok = True
    total_cycles = total_delta = 0
    for row in rows:
        cycles = int(row["degraded_cycles"] or 0)
        tenants = int(row["tenants"] or 0)
        before = int(row["msgs_before"] or 0)
        after = int(row["msgs_after"] or 0)
        delta = after - before
        expected = cycles * tenants
        slack = 3 * tenants
        total_cycles += cycles
        total_delta += delta
        verdict = "OK"
        if cycles == 0 or tenants == 0:
            verdict, ok = "VACUOUS (nothing degraded)", False
        elif delta < expected:
            verdict, ok = f"MISSING {expected - delta} record(s)", False
        elif delta > expected + slack:
            verdict, ok = f"{delta - expected} above expectation (> {slack} slack)", False
        print(f"  {row['label']}: {cycles} cycle(s) x {tenants} tenant(s) "
              f"= {expected} expected, stream grew {delta}  -> {verdict}")

    print(f"  totals: {total_cycles} degraded cycle(s), {total_delta} record(s) "
          f"across {len(rows)} window(s)")
    if total_cycles == 0 or total_delta == 0:
        print("  SK-H2: VACUOUS — nothing degraded, so nothing was audited")
        return False
    return ok


def main() -> int:
    buckets = load_buckets()
    timeline = load_timeline()
    print(f"Stage B post-hoc scoring — {len(buckets)} buckets, "
          f"{len(timeline)} timeline events\n")

    print("SK-H1 latency control (netem):")
    h1 = score_latency(buckets, timeline)
    print("\nSK-H2 audit continuity:")
    h2 = score_audit()

    print("\n=== VERDICT ===")
    print(f"  SK-H1 can be made to fail : {'YES' if h1 else 'NO'}")
    print(f"  SK-H2 has real counts     : {'YES' if h2 else 'NO'}")
    ok = h1 and h2
    print(f"  Stage B: {'PASS — hypotheses are instrumented' if ok else 'FAIL — a hypothesis still cannot fail'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
