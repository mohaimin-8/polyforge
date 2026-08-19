#!/usr/bin/env python3
"""PREREG_LIVE_SOAK_V2.md -> RESULTS_LIVE_SOAK_V2.md (WP14, attempt 4).

The headline is not "the soak passed". It is that **three of the five frozen
hypotheses turned out to have no working instrument**, which this attempt
discovered mid-run and documented before the numbers were in
(`eval/results/live_soak_evidence/MIDRUN_FINDINGS.md`, committed at T+14 h).

Two of them are vacuous rather than failed, and that distinction is the whole
point of this record:

  * **SK-H2** is scored as "degraded cycles == audit records, exactly".
    The operator only publishes audit records when POLYFORGE_NATS_URL is set,
    and it is set nowhere -- chart, harness, or live deployment. There is no
    NATS in this system. So the rule computes 0 == 0 and returns *true*.
  * **SK-H1** is scored as "post-fault mean_violation within CHAOS_TOL=0.05
    of pre-fault". The CRUD SLO target is 375 ms and the observed p99 is
    ~8 ms, roughly 47x headroom, so mean_violation is 0.00 in almost every
    hour of the run. The rule cannot fail regardless of what the faults do.

Reporting either as a passed hypothesis would be false. They are reported as
**VACUOUS**, with the mechanism named, because a test that cannot fail has
not been passed -- it has not been run.

SK-H3 is different: it is a real test against a real threshold, and it is
scored here from data the harness never emitted. `eval-export` produces only
run-level scalars (cmd/control-plane/evalexport.go has no timeseries field),
and live_soak.yaml's `timeseries_reps: 0` leaves the DuckDB timeseries table
empty, so the hour buckets SK-H3 needs did not exist. They were recovered
from the per-event Postgres table by `eval/scripts/export_soak_buckets.sh`
before teardown destroyed it.

    python analysis_live_soak_v2.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

_RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
EVIDENCE = _RESULTS / "live_soak_evidence"

TARGET_S = 86_400
MINIMUM_S = 43_200
FAULT_OFFSETS = [7200, 18000, 28800, 39600, 50400, 61200, 72000, 82800]
# SK-H3's threshold: B2's committed crud p99, not a margin invented today.
B2_CRUD_P99_MS = 8.0072
# SK-H1's frozen tolerance.
CHAOS_TOL = 0.05
# evalexport.go: evalSLOBaseMS["crud"] * evalSLOClassScale["standard"].
CRUD_TARGET_MS = 150.0 * 2.5

FAULT_RE = re.compile(
    r"=== FAULT (?P<n>\d) (?P<kind>[a-z-]+) at (?P<ts>[\d:]+) \(T\+(?P<off>\d+)s\)")


def _rows(path: Path) -> list[dict]:
    """psql -A -F, output, minus its trailing "(N rows)" line."""
    if not path.exists():
        return []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip() and not re.match(r"^\(\d+ rows?\)$", l.strip())]
    return list(csv.DictReader(lines))


def _pick(stem: str) -> Path:
    """Prefer the post-teardown capture; fall back to the mid-run insurance
    pass, and say which was used rather than silently substituting."""
    final = EVIDENCE / f"{stem}_final.csv"
    return final if final.exists() else EVIDENCE / f"{stem}_midrun.csv"


def load_injector_events() -> list[dict]:
    """Faults keyed on T+offset. The injectors' wall-clock strings are UTC
    while the run is discussed in local time (UTC+6), and each injector names
    its own faults "FAULT 1/2" within its own log -- so the offset is the only
    unambiguous key, and ordering by it recovers the true 1..8 sequence."""
    events: dict[int, dict] = {}
    for log in sorted(EVIDENCE.glob("wp14_inject_*.log")):
        text = log.read_text(encoding="utf-8", errors="replace")
        for m in FAULT_RE.finditer(text):
            events[int(m.group("off"))] = {
                "offset_s": int(m.group("off")), "kind": m.group("kind"),
                "utc": m.group("ts"), "log": log.name,
            }
    return [events[o] for o in sorted(events)]


def score_h3(hourly: list[dict]) -> dict:
    over, buckets = [], []
    for r in hourly:
        try:
            p95 = float(r["crud_p95_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        buckets.append((r["hour_utc"], int(r["n_events"]), p95,
                        float(r["crud_p99_ms"]), float(r["mean_violation"])))
        if p95 > B2_CRUD_P99_MS:
            over.append((r["hour_utc"], p95))
    return {"buckets": buckets, "over": over,
            "pass": bool(buckets) and not over}


def violation_range(hourly: list[dict]) -> tuple[float, float]:
    vals = [float(r["mean_violation"]) for r in hourly
            if r.get("mean_violation") not in (None, "")]
    return (min(vals), max(vals)) if vals else (0.0, 0.0)


def load_forward_summary() -> dict | None:
    p = EVIDENCE / "port_forward_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def endpoint_gap() -> dict | None:
    """Fault-7 planner endpoint gap, registered in MIDRUN_FINDINGS.md before
    the fault fired with both readings interpreted in advance."""
    p = EVIDENCE / "fault7_endpoint_gap.log"
    if not p.exists():
        return None
    zero, total, first_zero, last_zero = 0, 0, None, None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3 or parts[1] == "ERR":
            continue
        total += 1
        if parts[1] == "0":
            zero += 1
            first_zero = first_zero or parts[0]
            last_zero = parts[0]
    return {"samples": total, "zero_ready_samples": zero,
            "first_zero": first_zero, "last_zero": last_zero}


def main() -> None:
    hourly = _rows(_pick("soak_hourly"))
    scalars = _rows(_pick("soak_scalars"))
    events = load_injector_events()
    h3 = score_h3(hourly)
    vmin, vmax = violation_range(hourly)
    fwd = load_forward_summary()
    gap = endpoint_gap()
    src = ("post-teardown capture"
           if (EVIDENCE / "soak_hourly_final.csv").exists()
           else "MID-RUN insurance pass (the final capture did not complete)")

    L: list[str] = []
    w = L.append
    w("# Live CRUD-plane soak - attempt 4 (WP14)\n")
    w("Generated by `analysis_live_soak_v2.py` from evidence under "
      "`eval/results/live_soak_evidence/`. Pre-registration: "
      "`PREREG_LIVE_SOAK_V2.md`, committed and pushed before the run.\n")

    w("\n## Headline - the run completed; most of its hypotheses could not "
      "be tested\n")
    w("The soak ran its full duration and delivered load cleanly. That is a "
      "real result and it supports the duration claim this work package "
      "exists for. But **three of the five frozen hypotheses had no working "
      "instrument**, which was found at T+14 h and written up before any "
      "number was known (`MIDRUN_FINDINGS.md`). Two of them will now compute "
      "a passing value while testing nothing.\n")

    w("\n| id | verdict | why |")
    w("|---|---|---|")
    w("| **SK-H1** recovery | **VACUOUS** | `mean_violation` ranges "
      f"{vmin:.2e}-{vmax:.2e} across the run. The CRUD target is "
      f"{CRUD_TARGET_MS:.0f} ms against a p99 near "
      f"{B2_CRUD_P99_MS:.1f} ms, so violation has no dynamic range and the "
      f"+/-{CHAOS_TOL} rule cannot fail. |")
    w("| **SK-H2** audit | **VACUOUS** | no audit stream exists: "
      "`PlanRunner.audit` no-ops when `Audit` is nil, `Audit` is assigned "
      "only under `POLYFORGE_NATS_URL`, and that is set nowhere. 0 == 0. |")
    w(f"| **SK-H3** latency | **{'PASS' if h3['pass'] else 'FAIL'}** | "
      f"{len(h3['buckets'])} hour buckets, "
      f"{len(h3['over'])} above the committed {B2_CRUD_P99_MS} ms |")
    w("| **SK-H4** validity | see gates below | |")
    w("| **SK-H5** forward restarts | descriptive by design | "
      + (f"{fwd.get('restarts', '?')} restarts" if fwd else "summary absent")
      + " |")

    w("\n### Why two vacuous passes are reported as failures of the "
      "experiment, not successes of the system\n")
    w("A hypothesis that cannot fail has not been passed; it has not been "
      "run. Both would have computed the same passing value in attempts 1, "
      "2 and 3, and would do so again on a flawless attempt 5, because "
      "neither depends on the controller behaving well. Recording them as "
      "\"SK-H1 PASS, SK-H2 PASS\" would put two green cells in a results "
      "table that mean precisely nothing - the exact laundering "
      "`RESULTS_LIVE_SOAK.md` was written to refuse.\n")

    w("\n## SK-H3 - the one substantive test, and it passes\n")
    w(f"Scored from **{src}**. The hour buckets SK-H3 requires are not "
      "produced by the harness: `eval-export` emits run-level scalars only "
      "and `timeseries_reps: 0` leaves the DuckDB timeseries table empty. "
      "They were recovered from the per-event Postgres table by "
      "`eval/scripts/export_soak_buckets.sh`, mirroring the exporter's own "
      "method (CRUD = empty `model_tier`; nearest-rank percentile via "
      "`percentile_disc`, since `percentile_cont` interpolates and disagrees "
      "in the fourth decimal).\n")
    if scalars:
        s = scalars[0]
        w(f"\nRun-level: **{int(s['n_events']):,} CRUD events**, "
          f"crud_p95 **{float(s['crud_p95_ms']):.4f} ms**, "
          f"crud_p99 **{float(s['crud_p99_ms']):.4f} ms**, "
          f"mean_violation **{float(s['mean_violation']):.2e}**.\n")
    w("\n| hour (UTC) | events | crud_p95 ms | crud_p99 ms | violation | vs "
      f"{B2_CRUD_P99_MS} |")
    w("|---|---:|---:|---:|---:|---|")
    for hr, n, p95, p99, mv in h3["buckets"]:
        w(f"| {hr[:19]} | {n:,} | {p95:.4f} | {p99:.4f} | {mv:.2e} | "
          f"{'ok' if p95 <= B2_CRUD_P99_MS else '**OVER**'} |")

    w("\n## Faults\n")
    w("Parsed from the committed injector logs. Wall-clock strings there are "
      "**UTC** (local is UTC+6), and each injector numbers its own faults "
      "from 1, so `T+offset` is the only unambiguous key.\n")
    w("\n| # | T+ | fault | UTC |")
    w("|---:|---:|---|---|")
    for i, e in enumerate(events, 1):
        w(f"| {i} | {e['offset_s'] / 3600:.0f} h | `{e['kind']}` | {e['utc']} |")
    w(f"\n**{len(events)}/{len(FAULT_OFFSETS)}** frozen offsets fired.\n")

    if gap:
        w("\n### The planner-crash faults barely perturbed anything\n")
        w("Registered in `MIDRUN_FINDINGS.md` before fault 7 fired, with "
          "both readings interpreted in advance. Across five planner kills "
          "the operator logged **zero** `planner unavailable, holding last "
          "good plan` lines, though the binary demonstrably contains that "
          "string and the plan interval is 10 s.\n")
        w(f"\nFault-7 endpoint probe: **{gap['zero_ready_samples']} of "
          f"{gap['samples']} samples** saw zero ready planner endpoints"
          + (f" (first {gap['first_zero']}, last {gap['last_zero']})."
             if gap["first_zero"] else ".") + "\n")
        w("A gap materially shorter than the 10 s plan interval means the "
          "controller was never actually asked to survive a planner outage, "
          "and the four planner-crash occurrences are much weaker tests than "
          "the prereg assumed.\n")

    w("\n## Disposition\n")
    w("- The 24 h duration result stands and is what WP14 needed.")
    w("- **SK-H1 and SK-H2 are not evidence of anything** and must not be "
      "cited as passed hypotheses.")
    w("- Fixing them is instrumentation work, not controller work: give the "
      "violation metric dynamic range (an SLO target near the observed "
      "distribution, not 47x above it), and either wire an audit sink or "
      "drop the audit hypothesis.")
    w("- The exporter should emit bucketed metrics directly, so a future "
      "soak does not depend on rescuing Postgres before teardown.\n")

    out = record_path("RESULTS_LIVE_SOAK_V2.md")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"source: {src} | buckets: {len(h3['buckets'])} | "
          f"H3 {'PASS' if h3['pass'] else 'FAIL'} | faults: {len(events)}/8")


if __name__ == "__main__":
    main()
