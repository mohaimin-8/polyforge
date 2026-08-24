#!/usr/bin/env python3
"""PREREG_LIVE_SOAK_V2.md -> RESULTS_LIVE_SOAK_V2.md (WP14, attempt 4).

**This record reports an INVALID run — the third SK-H4 failure in four
sittings.** The soak completed its full 24 h and was rejected by the harness's
own delivery gate at 4.567% failed requests and 236,566 dropped iterations.

Per PREREG_LIVE_SOAK_V2 §Outcome handling, a third SK-H4 failure makes the
inability to sustain a 24 h live run *itself* the finding, rather than
something to keep retrying past. This record therefore stops the retry loop
and names the mechanism, which attempt 4 established and earlier attempts did
not: `kubectl port-forward` pins to one pod out of sixteen, that pod OOMs on
its 256 Mi limit under the whole workload, and its death takes the load path
down with it, 282 times.

Beyond SK-H4, three of the five frozen hypotheses had no working instrument —
found at T+14 h and documented before any number was known
(`eval/results/live_soak_evidence/MIDRUN_FINDINGS.md`):

  * **SK-H2** computes 0 == 0 because no audit stream exists at all: the
    operator publishes audit records only when POLYFORGE_NATS_URL is set, and
    it is set nowhere — chart, harness, or live deployment.
  * **SK-H1** is scored on post-fault `mean_violation` moving less than
    CHAOS_TOL. The CRUD target is 375 ms against an ~8 ms p99, so violation is
    0.00 in almost every hour and the rule cannot fail. Independently, its
    recovery path never executed: across seven planner-crash injections in
    four attempts, `fallback()` has produced zero log lines
    (`FAULT7_ENDPOINT_MEASUREMENT.md`).
  * **SK-H3** needed hour buckets the harness never emits, recovered from
    Postgres before teardown by `eval/scripts/export_soak_buckets.sh`.

A test that cannot fail has not been passed; it has not been run. SK-H1 and
SK-H2 are reported as VACUOUS, never as PASS.

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
# Attempt 4's evidence, moved aside when attempt 5 reused the live_soak_evidence
# path (session 39). Attempt 3's evidence was LOST that way earlier in this work
# package, so the move is deliberate and this pointer follows it. The record
# this script generates is frozen and must keep rebuilding byte-identically
# from attempt 4's data, not from whichever sitting ran last.
EVIDENCE = _RESULTS / "live_soak_attempt4_evidence"
RUNNER_LOG = _RESULTS / "live_soak_attempt4_runner.log"

TARGET_S = 86_400
FAULT_OFFSETS = [7200, 18000, 28800, 39600, 50400, 61200, 72000, 82800]
# SK-H3's threshold: B2's committed crud p99, not a margin invented today.
B2_CRUD_P99_MS = 8.0072
CHAOS_TOL = 0.05
# evalexport.go: evalSLOBaseMS["crud"] * evalSLOClassScale["standard"].
CRUD_TARGET_MS = 150.0 * 2.5
# cluster_backend.check_k6_delivery.
MAX_FAILED_RATE = 0.01
MAX_DROPPED = 1

FAULT_RE = re.compile(
    r"=== FAULT (?P<n>\d) (?P<kind>[a-z-]+) at (?P<ts>[\d:]+) \(T\+(?P<off>\d+)s\)")


def _rows(path: Path) -> list[dict]:
    """psql -A -F, output, minus its trailing "(N rows)" line."""
    if not path.exists():
        return []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip() and not re.match(r"^\(\d+ rows?\)$", l.strip())]
    return list(csv.DictReader(lines))


def _pick(stem: str) -> tuple[Path, bool]:
    """The post-teardown capture if it exists, else the mid-run insurance pass.
    Returns which, so the record states its provenance instead of quietly
    substituting a shorter window for the full one."""
    final = EVIDENCE / f"{stem}_final.csv"
    if final.exists():
        return final, True
    return EVIDENCE / f"{stem}_midrun.csv", False


def load_injector_events() -> list[dict]:
    """Faults keyed on T+offset. The injectors' wall-clock strings are UTC
    while the run is discussed in local time (UTC+6), and each injector names
    its own faults "FAULT 1/2" within its own log — so the offset is the only
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


def load_k6() -> dict:
    """Delivery metrics as k6 exported them.

    `http_req_failed` is a k6 Rate: `passes` counts the times the condition
    ("this request failed") held, so passes IS the failed-request count and
    fails is the successful one. Reading them the other way round inverts a
    4.6% failure into a 95% one, so they are named explicitly here.
    """
    p = EVIDENCE / "k6-summary.json"
    if not p.exists():
        return {}
    m = json.loads(p.read_text(encoding="utf-8")).get("metrics", {})
    failed = m.get("http_req_failed", {})
    return {
        "failed_rate": failed.get("value"),
        "failed_count": failed.get("passes"),
        "ok_count": failed.get("fails"),
        "reqs": m.get("http_reqs", {}).get("count"),
        "req_rate": m.get("http_reqs", {}).get("rate"),
        "dropped": m.get("dropped_iterations", {}).get("count"),
        "client_p95_ms": m.get("http_req_duration", {}).get("p(95)"),
        "client_avg_ms": m.get("http_req_duration", {}).get("avg"),
        "client_max_ms": m.get("http_req_duration", {}).get("max"),
        "vus_max": m.get("vus_max", {}).get("value"),
    }


def load_runner_verdict() -> dict:
    if not RUNNER_LOG.exists():
        return {}
    text = RUNNER_LOG.read_text(encoding="utf-8", errors="replace")
    start = text.find("{")
    if start < 0:
        return {}
    return json.loads(text[start:text.index("}", start) + 1])


def load_forward_summary() -> dict:
    p = EVIDENCE / "port_forward_summary.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    reasons: dict[str, int] = {}
    for e in d.get("events", []):
        m = re.search(r"RESTART #\d+ \(([^)]+)\)", e)
        if m:
            reasons[m.group(1)] = reasons.get(m.group(1), 0) + 1
    return {"restarts": d.get("restarts"),
            "probe_failures": d.get("probe_failures"), "reasons": reasons}


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
    return {"buckets": buckets, "over": over, "pass": bool(buckets) and not over}


def endpoint_gap() -> dict | None:
    """Fault-7 planner endpoint gap, registered in MIDRUN_FINDINGS.md before
    the fault fired with both readings interpreted in advance."""
    p = EVIDENCE / "fault7_endpoint_gap.log"
    if not p.exists():
        return None
    zero = [l.split()[0] for l in p.read_text(encoding="utf-8").splitlines()
            if not l.startswith("#") and len(l.split()) > 2
            and l.split()[1] == "0"]
    if not zero:
        return {"span_s": 0, "first": None, "last": None}
    import datetime
    a = datetime.datetime.fromisoformat(zero[0])
    b = datetime.datetime.fromisoformat(zero[-1])
    return {"span_s": (b - a).total_seconds(), "first": zero[0], "last": zero[-1]}


def main() -> None:
    hourly_path, hourly_is_final = _pick("soak_hourly")
    hourly = _rows(hourly_path)
    scalars = _rows(_pick("soak_scalars")[0])
    events = load_injector_events()
    h3 = score_h3(hourly)
    k6 = load_k6()
    verdict = load_runner_verdict()
    fwd = load_forward_summary()
    gap = endpoint_gap()
    vmin = min((float(r["mean_violation"]) for r in hourly
                if r.get("mean_violation")), default=0.0)
    vmax = max((float(r["mean_violation"]) for r in hourly
                if r.get("mean_violation")), default=0.0)

    h4_pass = bool(k6) and k6["failed_rate"] < MAX_FAILED_RATE \
        and k6["dropped"] < MAX_DROPPED

    L: list[str] = []
    w = L.append
    w("# Live CRUD-plane soak — attempt 4: the run is INVALID (WP14)\n")
    w("Generated by `analysis_live_soak_v2.py` from evidence committed under "
      "`eval/results/live_soak_attempt4_evidence/`. Pre-registration: "
      "`PREREG_LIVE_SOAK_V2.md`, committed and pushed before the run.\n")

    # --- headline ---------------------------------------------------------
    w("\n## Headline — SK-H4 FAILS, for the third time in four sittings\n")
    if k6:
        w(f"The soak ran its full **{TARGET_S // 3600} h** "
          f"({verdict.get('_elapsed', '86,705')} s wall), injected all "
          f"{len(events)} frozen faults, and was rejected by "
          "`cluster_backend.check_k6_delivery`:\n")
        w("\n| gate | observed | threshold | verdict |")
        w("|---|---:|---:|---|")
        w(f"| `http_req_failed` | **{k6['failed_rate']:.4%}** "
          f"({k6['failed_count']:,} of {k6['reqs']:,}) | < 1% | **FAIL** |")
        w(f"| `dropped_iterations` | **{k6['dropped']:,}** | < 1 | **FAIL** |")
    if verdict:
        w("\n| runner verdict | value |")
        w("|---|---:|")
        for key in ("expected_runs", "valid_runs", "failed_runs", "null_metrics"):
            if key in verdict:
                w(f"| `{key}` | {verdict[key]} |")
        w(f"| `ok` | **{str(verdict.get('ok')).lower()}** |")
    w("\nNo `eval-export.json` was preserved, so the run recorded **no metrics "
      "rows**. As in attempt 2, this is not a weak result but an absent one.\n")

    # --- the mechanism ----------------------------------------------------
    w("\n## The mechanism, which attempt 4 established\n")
    w("Earlier sittings recorded *that* delivery failed. This one recorded "
      "*why*, and the two vantage points on the same traffic make it "
      "unmistakable:\n")
    if k6 and scalars:
        s = scalars[0]
        w("\n| measured at | p95 | includes |")
        w("|---|---:|---|")
        w(f"| server (Postgres `telemetry_events`) | "
          f"**{float(s['crud_p95_ms']):.2f} ms** | control-plane handling only |")
        w(f"| client (k6 `http_req_duration`) | "
          f"**{k6['client_p95_ms']:.0f} ms** | + `kubectl port-forward` |")
        ratio = k6["client_p95_ms"] / float(s["crud_p95_ms"])
        w(f"\nA **{ratio:.0f}x** gap. The control plane served requests in "
          f"~{float(s['crud_p95_ms']):.1f} ms throughout; the load path in "
          f"front of it reached {k6['client_max_ms'] / 1000:.0f} s at worst.\n")
    w("`kubectl port-forward service/…` resolves to **one pod** when it "
      "starts and never follows the Service. All ~300 req/s therefore landed "
      "on a single replica out of sixteen, which climbed to its 256 Mi limit, "
      "was OOM-killed, took the forward down with it, and was replaced by a "
      "fresh pod that repeated the cycle.\n")
    if fwd:
        split = ", ".join(f"{v} after {k}"
                          for k, v in sorted(fwd["reasons"].items()))
        w(f"\n**{fwd['restarts']} forward restarts** over the run — {split} "
          f"— against {fwd['probe_failures']} health-probe failures.\n")
    w("\nA sixteen-replica deployment behind a port-forward has the fault "
      "tolerance of **one replica**, and no health signal reports it: pods "
      "stay `Running`, the Service keeps sixteen endpoints, and `/healthz` "
      "answers 200 between kills. The 5-minute delivery probe called the "
      "system healthy for 100 minutes while pods were dying every 2 minutes.\n")
    w("**This is a property of the evaluation harness's load path, not of the "
      "controller.** It says nothing about PolyForge behind a Service or "
      "Ingress that balances across endpoints, and \"PolyForge OOMs under "
      "sustained load\" would be the wrong conclusion to draw from it.\n")

    # --- hypotheses -------------------------------------------------------
    w("\n## Hypotheses\n")
    w("\n| id | verdict | basis |")
    w("|---|---|---|")
    w(f"| **SK-H4** validity | **FAIL** | {k6['failed_rate']:.3%} failed, "
      f"{k6['dropped']:,} dropped |" if k6 else
      "| **SK-H4** validity | **FAIL** | k6 summary absent |")
    w(f"| **SK-H3** latency | **{'PASS' if h3['pass'] else 'FAIL'}** | "
      f"{len(h3['buckets'])} hour buckets, {len(h3['over'])} above "
      f"{B2_CRUD_P99_MS} ms |")
    w("| **SK-H1** recovery | **VACUOUS** | "
      f"`mean_violation` spans {vmin:.2e}–{vmax:.2e}; a "
      f"{CRUD_TARGET_MS:.0f} ms target against an ~8 ms p99 leaves the "
      f"±{CHAOS_TOL} rule unable to fail. Its recovery path never ran: zero "
      "`fallback()` lines across seven planner kills. |")
    w("| **SK-H2** audit | **VACUOUS** | no audit stream exists; "
      "`PlanRunner.audit` no-ops when `Audit` is nil and `POLYFORGE_NATS_URL` "
      "is set nowhere. 0 == 0. |")
    if fwd:
        w(f"| **SK-H5** forward restarts | descriptive | {fwd['restarts']} "
          "restarts — **the OOM cycle, not supervisor merit** |")

    w("\n### Two vacuous passes are failures of the experiment, not "
      "successes of the system\n")
    w("A hypothesis that cannot fail has not been passed; it has not been "
      "run. SK-H1 and SK-H2 would have computed the same passing value in "
      "attempts 1, 2 and 3, and would again on a flawless attempt 5, because "
      "neither depends on the controller behaving well. Recording them as "
      "\"PASS\" would put two green cells in a results table that mean "
      "precisely nothing.\n")
    w("**SK-H5 is contaminated and must not be read as a result.** It was "
      "registered as descriptive, and until T+17.5 h its answer was *zero* "
      "restarts — which the prereg had already fixed as meaning the attempt-2 "
      "port-forward diagnosis stays unconfirmed. The final count describes "
      "the OOM cycle destroying the forward's target, and in part an observer "
      "that ran local builds on the same laptop "
      "(`INCIDENT_OBSERVER_CONTENTION.md`). It is not evidence the supervisor "
      "mechanism proved its worth.\n")

    # --- SK-H3 ------------------------------------------------------------
    w("\n## SK-H3 — the one substantive test, and it passes\n")
    if not hourly_is_final:
        w("> **Provenance.** Scored from the **mid-run insurance pass "
          "(hours 0–16)**, not the full run. The post-teardown capture lost "
          "its race: `cluster_backend` runs `eval-export` then "
          "`kind delete cluster`, and both had completed before the "
          "end-of-run watcher's 10 s poll noticed k6 had exited. Hours 17–24 "
          "of hour-bucket data — the OOM period — are unrecoverable, because "
          "deleting the cluster destroys the Postgres holding them. The "
          "mid-run pass exists precisely because that race was judged losable "
          "in advance.\n")
    w("The hour buckets SK-H3 requires are not produced by the harness at "
      "all: `eval-export` emits run-level scalars and has no timeseries "
      "field, and `live_soak.yaml`'s `timeseries_reps: 0` leaves the DuckDB "
      "timeseries table empty. They were recovered from the per-event "
      "Postgres table by `eval/scripts/export_soak_buckets.sh`, mirroring the "
      "exporter's own method — CRUD is empty `model_tier`, and the percentile "
      "is nearest-rank via `percentile_disc`, since `percentile_cont` "
      "interpolates and disagrees in the fourth decimal.\n")
    if scalars:
        s = scalars[0]
        w(f"\nServer-side over the captured window: "
          f"**{int(s['n_events']):,} CRUD events**, crud_p95 "
          f"**{float(s['crud_p95_ms']):.4f} ms**, crud_p99 "
          f"**{float(s['crud_p99_ms']):.4f} ms** (B2's committed p99 is "
          f"{B2_CRUD_P99_MS} ms), mean_violation "
          f"**{float(s['mean_violation']):.2e}**.\n")
    w(f"\n| hour (UTC) | events | crud_p95 ms | crud_p99 ms | violation | vs "
      f"{B2_CRUD_P99_MS} |")
    w("|---|---:|---:|---:|---:|---|")
    for hr, n, p95, p99, mv in h3["buckets"]:
        w(f"| {hr[:19]} | {n:,} | {p95:.4f} | {p99:.4f} | {mv:.2e} | "
          f"{'ok' if p95 <= B2_CRUD_P99_MS else '**OVER**'} |")

    # --- faults -----------------------------------------------------------
    w("\n## Faults — all eight fired\n")
    w("Parsed from the committed injector logs. Their wall-clock strings are "
      "**UTC** (local is UTC+6), and each injector numbers its own faults "
      "from 1, so `T+offset` is the only unambiguous key.\n")
    w("\n| # | T+ | fault | UTC |")
    w("|---:|---:|---|---|")
    for i, e in enumerate(events, 1):
        w(f"| {i} | {e['offset_s'] / 3600:.0f} h | `{e['kind']}` | {e['utc']} |")
    ok = [e["offset_s"] for e in events] == FAULT_OFFSETS
    w(f"\n**{len(events)}/{len(FAULT_OFFSETS)}** frozen offsets fired"
      + (" exactly as scheduled.\n" if ok else " — schedule deviated.\n"))

    if gap:
        w("\n### The planner-crash faults never interrupted planning\n")
        w("Registered in `MIDRUN_FINDINGS.md` **before** fault 7 fired, with "
          "both readings interpreted in advance. The prediction was that the "
          "planner's endpoint gap is shorter than the 10 s plan interval, "
          "which would explain five planner kills producing zero fallback "
          "lines.\n")
        w(f"\nMeasured gap: **{gap['span_s']:.0f} s**, one contiguous span — "
          "2.6x the plan interval. Fallback lines produced: **0**. The "
          "registered prediction is falsified, and the alternative reading "
          "applies as registered.\n")
        w("The probe's own state column carries the likely mechanism: the pod "
          "reported **1/1 ready throughout termination**. Kubernetes delists "
          "a deleting pod from `Endpoints` at once but does not close "
          "established connections, and the operator reaches the planner over "
          "a reused HTTP connection — so it kept talking to the dying pod and "
          "saw no outage. Recorded as inference; confirming it needs a "
          "connection-level test that could not run against a live soak.\n")

    # --- disposition ------------------------------------------------------
    w("\n## Disposition — the retry loop stops here\n")
    w("`PREREG_LIVE_SOAK_V2.md` §Outcome handling fixed this response before "
      "the run: *\"A third failure would make 'the local harness cannot "
      "sustain a 24 h live run on this machine' itself the finding worth "
      "reporting, rather than something to keep retrying past.\"* This is the "
      "third SK-H4 failure. The loop stops.\n")
    w("That framing is sharpened by what this attempt established. It is not "
      "that the machine is inadequate — the control plane served 25.6M "
      "requests at a 2.2 ms server-side p95 while being asked to. It is that "
      "**`kubectl port-forward` cannot carry 300 req/s for 24 h**, and that "
      "pinning the entire workload to one of sixteen replicas drives that pod "
      "into an OOM cycle. That is specific and fixable, where \"the laptop "
      "isn't good enough\" would not have been.\n")
    w("\nWhat a fifth sitting would need, before it would be worth running:\n")
    w("- **A load path that is not a port-forward** — NodePort, Ingress, or "
      "an in-cluster generator — so traffic reaches all sixteen replicas and "
      "no single pod carries the workload.")
    w("- **A violation metric with dynamic range.** A 375 ms target against "
      "an 8 ms p99 makes SK-H1 unfailable; the target has to sit near the "
      "observed distribution.")
    w("- **An audit sink, or no audit hypothesis.** SK-H2 cannot be scored "
      "while `POLYFORGE_NATS_URL` is unset everywhere.")
    w("- **A planner fault that actually stops the planner** — "
      "`--grace-period=0` or scaling to zero — since deletion alone leaves it "
      "serving established connections.")
    w("- **Bucketed metrics from `eval-export` directly**, so a run does not "
      "depend on rescuing Postgres before teardown.")
    w("- **No local builds or heavy queries for the duration**, a protocol "
      "rule this run did not have "
      "(`INCIDENT_OBSERVER_CONTENTION.md`).\n")
    w("The 24 h duration itself is not in doubt: the run reached its target "
      "and the fault schedule executed in full. What is in doubt is whether "
      "anything was measured, and for four of five hypotheses the answer is "
      "no.\n")

    out = record_path("RESULTS_LIVE_SOAK_V2.md")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"SK-H4 {'PASS' if h4_pass else 'FAIL'} | "
          f"SK-H3 {'PASS' if h3['pass'] else 'FAIL'} | "
          f"buckets {len(h3['buckets'])} ({'final' if hourly_is_final else 'midrun'}) | "
          f"faults {len(events)}/8 | forward restarts {fwd.get('restarts')}")


if __name__ == "__main__":
    main()
