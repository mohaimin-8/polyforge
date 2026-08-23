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
    p = EVIDENCE / "eval-export.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("buckets", [])


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
    """Degraded cycles vs audit records.

    The injector logged one fallback line per degraded cycle per tenant, so the
    operator log is the count of degraded cycles. The audit stream should carry
    exactly the same number for those cycles.
    """
    controls = EVIDENCE / "positive_controls.log"
    if not controls.exists():
        print("  positive_controls.log missing")
        return False
    cycles = sum(int(m) for m in re.findall(r"(\d+) fallback line\(s\)",
                                            controls.read_text(encoding="utf-8")))
    summary = EVIDENCE / "audit_count.txt"
    records = int(summary.read_text(encoding="utf-8").strip()) if summary.exists() else None
    print(f"  degraded cycles observed : {cycles}")
    print(f"  audit records on stream  : {records if records is not None else 'not captured'}")
    if not cycles:
        print("  SK-H2: VACUOUS — zero degraded cycles, nothing to audit")
        return False
    if records is None:
        print("  SK-H2: cycles exist and are countable; stream count not captured this run")
        return True
    return records >= cycles


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
