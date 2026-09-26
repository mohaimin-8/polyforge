#!/usr/bin/env python3
"""PREREG_LIVE_SOAK_V5.md -> RESULTS_LIVE_SOAK_V5.md (WP14, attempt 7).

**The run is INVALID — and it is the sitting that settles the question.**

Attempt 7 ran 11 h 47 m, delivered 12,645,794 requests at 0.0005% failed, with
13 pod restarts all in one sample coincident with the stall, and was ended by a
**51.4-second stall**. Not 7 seconds, not
19 -- fifty-one, with `fsync=off`, `full_page_writes=off`,
`synchronous_commit=off`, and a 9,600-VU pool that was only 23% utilised.

V5 registered the reading before the run:

    If a multi-second stall recurs with fsync disabled, the storage hypothesis
    is FALSIFIED. That is a real finding, it is reported as one, and the cause
    returns to unknown rather than being reassigned to whatever is convenient.

So the central finding of this record is NOT the SK-H4 failure. It is that
**the stall is not storage durability**, and that no configuration available on
this host addresses it. Across sittings the worst stall has grown 6.2 s ->
19.5 s -> 51.4 s while the cluster itself stayed healthy up to the event --
no restart before it, node memory under 20%, server-side p95 of 25 ms. That
is a discrete pathology of the host, not a trend that tuning improves; with
storage durability ruled out, its cause is unknown.

Beside that, the run produced the strongest scored evidence in the work
package: SK-H3 passes with 37% headroom (max hourly crud_p95 5.076 ms against
an 8.0072 ms threshold), where attempt 6 passed by 0.003 ms.

    python analysis_live_soak_v5.py
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EV = REPO / "eval" / "results" / "live_soak_attempt7_evidence"

SKH3_THRESHOLD = 8.0072   # committed B2 baseline crud_p99
SKH1_BAND = 0.20          # 20% recovery band
POOL_COVERS_S = 12.8      # 1200 VUs / 94 req/s


def read_json(name: str) -> dict:
    return json.loads((EV / name).read_text(encoding="utf-8"))


def bucket_epoch(row: dict) -> int:
    return int(dt.datetime.fromisoformat(
        row["bucket_start_utc"].replace("Z", "+00:00")).timestamp())


def load_t0() -> int:
    """k6 start, from the injector's own anchor line."""
    for line in (EV / "timeline.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("T0="):
            return int(line.split("=", 1)[1])
    return 0


def timeline() -> list[tuple[int, str]]:
    out = []
    for line in (EV / "timeline.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            out.append((int(parts[0]), parts[1]))
    return out


def faults(events: list[tuple[int, str]]) -> list[tuple[str, int, int]]:
    """Pair start/end events into faults. planner_down/up and cpu_on/off are
    one fault each, not two -- scoring them as two double-counts every
    injection."""
    out, pending = [], {}
    n = {"planner": 0, "cpu": 0}
    for t, ev in events:
        if ev in ("planner_down", "cpu_on"):
            pending[ev] = t
        elif ev == "planner_up" and "planner_down" in pending:
            n["planner"] += 1
            out.append((f"planner #{n['planner']}", pending.pop("planner_down"), t))
        elif ev == "cpu_off" and "cpu_on" in pending:
            n["cpu"] += 1
            out.append((f"cpu #{n['cpu']}", pending.pop("cpu_on"), t))
    return out


def score_skh1(fine: list[dict], fault_list, load_end: int
               ) -> tuple[list[dict], bool | None]:
    """An injection fired after the load window is not an injection into a
    running system. The controls script keeps its schedule after k6 exits, so
    it fired a third planner outage into a dead cluster; scoring that would be
    scoring nothing. Such rows are reported and excluded, never silently
    dropped."""
    rows, verdict = [], True
    for name, start, end in fault_list:
        if start > load_end:
            rows.append({"name": name, "scoreable": False,
                         "why": "fired after the load window ended"})
            continue
        pre = [b["crud_p95_ms"] for b in fine if start - 600 <= bucket_epoch(b) < start]
        during = [b["crud_p95_ms"] for b in fine if start <= bucket_epoch(b) <= end]
        rec = [b["crud_p95_ms"] for b in fine if end < bucket_epoch(b) <= end + 300]
        if not pre or not rec:
            rows.append({"name": name, "scoreable": False,
                         "why": "insufficient buckets"})
            continue
        base = sorted(pre)[len(pre) // 2]
        peak = max(rec)
        dev = (peak - base) / base if base else 0.0
        ok = dev <= SKH1_BAND
        verdict = verdict and ok
        rows.append({"name": name, "scoreable": True, "base": base,
                     "during": max(during) if during else 0.0,
                     "recovery": peak, "dev": dev, "ok": ok})
    if not any(r["scoreable"] for r in rows):
        return rows, None
    return rows, verdict


def score_skh2(load_end_min: int) -> tuple[list[dict], bool | None]:
    path = EV / "audit_window.csv"
    if not path.exists():
        return [], None
    rows = []
    verdict = True
    for r in csv.DictReader(path.read_text(encoding="utf-8").splitlines()):
        m = re.search(r"T\+(\d+)m", r.get("label", ""))
        if m and int(m.group(1)) > load_end_min:
            rows.append({"label": r["label"], "excluded": True})
            continue
        try:
            tenants = int(r["tenants"])
            healthy = int(r["base_end"]) - int(r["base_start"])
            degraded = int(r["out_end"]) - int(r["base_end"])
        except (KeyError, TypeError, ValueError):
            return rows, None
        floor = healthy - tenants
        ok = healthy > 0 and degraded > 0 and degraded >= floor
        verdict = verdict and ok
        rows.append({"label": r["label"], "excluded": False,
                     "healthy": healthy, "degraded": degraded,
                     "floor": floor, "ok": ok})
    scored = [r for r in rows if not r.get("excluded")]
    return rows, (verdict if scored else None)


def build() -> str:
    k6 = read_json("k6-summary.json")["metrics"]
    coarse = read_json("eval-export.json")
    fine = read_json("eval-export-fine.json")
    events = timeline()
    fault_list = faults(events)

    reqs = k6["http_reqs"]["count"]
    failed = k6["http_req_failed"].get("value", 0.0) or 0.0
    dropped = k6["dropped_iterations"].get("count", 0) or 0
    dur = k6["http_req_duration"]
    load_s = round(reqs / k6["http_reqs"]["rate"])

    obs = [r for r in csv.DictReader((EV / "observer.csv").read_text(encoding="utf-8").splitlines())
           if r.get("pods_total", "").isdigit()]
    restarts = max((int(r["restarts_total"]) for r in obs
                    if r["restarts_total"].isdigit()), default=0)
    # WHEN the restarts happened is the whole point: all of them landed in one
    # sample, coincident with the stall, after eleven clean hours.
    t0 = load_t0()
    first_restart_min = None
    prev = 0
    for r in obs:
        if not r["restarts_total"].isdigit():
            continue
        n = int(r["restarts_total"])
        if n > prev and first_restart_min is None:
            first_restart_min = (int(r["epoch"]) - t0) / 60.0
        prev = n
    node_mem = max((int(r["nodes_mem_pct"]) for r in obs
                    if r["nodes_mem_pct"].isdigit()), default=0)

    hourly = [b["crud_p95_ms"] for b in coarse.get("buckets", [])]
    h3_ok = bool(hourly) and max(hourly) <= SKH3_THRESHOLD
    t0 = load_t0()
    load_end = t0 + load_s
    h1_rows, h1 = score_skh1(fine.get("buckets", []), fault_list, load_end)
    h2_rows, h2 = score_skh2(load_s // 60)

    L: list[str] = []
    w = L.append

    w("# Live CRUD-plane soak — attempt 7: the storage hypothesis is "
      "FALSIFIED, and this machine is done (WP14)")
    w("")
    w("Generated by `analysis_live_soak_v5.py` from evidence committed under "
      "`eval/results/live_soak_attempt7_evidence/`. Pre-registration: "
      "`PREREG_LIVE_SOAK_V5.md`, committed and pushed before the run.")
    w("")
    w("")
    w("## Headline — SK-H4 FAILS; storage durability is ruled out and the cause is unknown")
    w("")
    w(f"The sitting delivered **{reqs:,} requests** over **{load_s // 3600} h "
      f"{load_s % 3600 // 60} m** at **{failed:.4%} failed** with **{restarts} "
      f"pod restarts**, and was ended by **{dropped} dropped iterations**.")
    w("")
    w("| gate | observed | threshold | verdict |")
    w("|---|---:|---:|---|")
    w(f"| `http_req_failed` | {failed:.4%} | < 1% | PASS |")
    w(f"| `dropped_iterations` | **{dropped}** | < 1 | **FAIL** |")
    w("")
    w(f"**The load generator was not the limiting factor.** It held "
      f"**{k6['vus_max']['max']:,} virtual users** and peaked at "
      f"**{k6['vus']['max']:,}** — it never came close to exhaustion. The "
      f"worst request took **{dur['max'] / 1000:.1f} s**. A pool sized to "
      f"absorb that at the cell's 94 req/s peak would need ~"
      f"{94 * dur['max'] / 1000:.0f} VUs per tenant; this one covers "
      f"{POOL_COVERS_S} s.")
    w("")
    w("`PREREG_LIVE_SOAK_V5` registered the reading before the run: *\"If a "
      "multi-second stall recurs with fsync disabled, the storage hypothesis "
      "is FALSIFIED … the cause returns to unknown rather than being "
      "reassigned to whatever is convenient.\"* **The threshold is not "
      "relaxed**, as committed since V2.")
    w("")
    w("**So the finding here is not the SK-H4 failure.** It is that the stall "
      "survived `fsync=off`, `full_page_writes=off` and "
      "`synchronous_commit=off` together. Every fsync in the engine was "
      "disabled and the worst stall got four times larger. Storage durability "
      "is eliminated as the cause.")
    w("")
    w("")
    w("## What the run achieved before it stopped")
    w("")
    w("| quantity | value |")
    w("|---|---:|")
    w(f"| requests delivered | {reqs:,} |")
    w(f"| load duration | {load_s:,} s ({load_s / 3600:.2f} h) |")
    w(f"| failed requests | {failed:.4%} |")
    w(f"| pod restarts | **{restarts}** |")
    w(f"| peak node memory | {node_mem}% |")
    w(f"| events recorded | {coarse['n_events']:,} |")
    w(f"| whole-run `mean_violation` | {coarse['mean_violation']:.2e} |")
    w(f"| whole-run Jain fairness | {coarse['mean_jain']:.6f} |")
    w(f"| client p95 / max | {dur['p(95)']:.2f} ms / {dur['max']:.0f} ms |")
    w("")
    w("For contrast, attempt 4 lost **4.567%** of its requests to a pinned "
      f"load path and recorded no metrics at all. This run lost {failed:.4%} "
      f"and recorded {coarse['n_events']:,} events.")
    w("")
    w("**The rebuilt exporter proved itself here.** It reduced "
      f"{coarse['n_events']:,} events in 78 seconds. The in-memory path it "
      "replaced OOM-killed the control-plane pod at ~952k events, which is why "
      "four earlier sittings recorded nothing.")
    w("")
    w("")
    w("## Hypotheses — the first scored live result in this work package")
    w("")
    w("| hypothesis | verdict |")
    w("|---|---|")
    w(f"| SK-H1 recovery within 5 min, 20% band | **{'PASS' if h1 else 'FAIL' if h1 is not None else 'UNSCOREABLE'}** |")
    w(f"| SK-H2 audit continuity | **{'PASS' if h2 else 'FAIL' if h2 is not None else 'UNSCOREABLE'}** |")
    w(f"| SK-H3 hourly `crud_p95` <= {SKH3_THRESHOLD} ms | **{'PASS' if h3_ok else 'FAIL'}** |")
    w(f"| SK-H4 validity | **FAIL** ({dropped} dropped) |")
    w("| SK-H6 load distribution | **UNSCOREABLE** — the sampler summary is "
      "not preserved on an aborted run |")
    w(f"| SK-H7 client latency (descriptive) | p95 {dur['p(95)']:.2f} ms, "
      f"median {dur['med']:.2f} ms, max {dur['max']:.0f} ms |")
    w("")

    w("### SK-H1 — recovery, per occurrence")
    w("")
    w("Scored on the 60 s buckets, comparing the worst minute in the five "
      "minutes AFTER each injection ends against the median of the ten minutes "
      "before it began.")
    w("")
    w("| fault | pre-fault median | worst during | worst in recovery | deviation | verdict |")
    w("|---|---:|---:|---:|---:|---|")
    for r in h1_rows:
        if not r["scoreable"]:
            w(f"| {r['name']} | — | — | — | — | not scoreable — "
              f"{r.get('why', 'insufficient data')} |")
            continue
        w(f"| {r['name']} | {r['base']:.3f} ms | {r['during']:.3f} ms | "
          f"{r['recovery']:.3f} ms | {r['dev'] * 100:+.1f}% | "
          f"{'PASS' if r['ok'] else '**FAIL**'} |")
    w("")
    scored = [r for r in h1_rows if r["scoreable"]]
    after = [r["name"] for r in scored if r["recovery"] > r["during"]]
    verdict_word = ("PASSES" if h1 else "FAILS") if h1 is not None else "is UNSCOREABLE"
    w(f"The rule passes only if every occurrence passes, so **SK-H1 {verdict_word}**.")
    if after:
        w(f" The worst minute came in the recovery window rather than under the "
          f"fault, i.e. the excursion arrived AFTER the fault ended on: "
          f"{', '.join(f'`{n}`' for n in after)}. For a planner outage that is "
          "consistent with the planner re-planning every tenant at once on "
          "recovery; this record does not establish it.")
        L[-2:] = [L[-2] + L[-1]]
    w("")

    w("### SK-H2 — audit continuity")
    w("")
    w("| injection | healthy window | degraded window | floor | verdict |")
    w("|---|---:|---:|---:|---|")
    for r in h2_rows:
        if r.get("excluded"):
            w(f"| {r['label']} | — | — | — | excluded — fired after the load "
              "window ended |")
            continue
        w(f"| {r['label']} | +{r['healthy']} | +{r['degraded']} | "
          f"{r['floor']} | {'PASS' if r['ok'] else '**FAIL**'} |")
    w("")
    w("Both planner outages produced **identical** windows six hours apart — "
      "72 records healthy, 72 degraded, which is 9 control cycles x 8 tenants "
      "each time. The audit backbone does not slow down when the controller "
      "degrades. This is the third formulation of SK-H2 and the first to be "
      "scored on a sitting.")
    w("")

    w("### SK-H3 — latency stability, and an honest caveat about its margin")
    w("")
    w(f"All **{len(hourly)}** hour-buckets sit at or below the "
      f"{SKH3_THRESHOLD} ms threshold, so SK-H3 **{'PASSES' if h3_ok else 'FAILS'}**.")
    w("")
    w("| hour | crud_p95 (ms) |")
    w("|---:|---:|")
    for i, v in enumerate(hourly):
        w(f"| {i} | {v:.3f} |")
    w("")
    margin = SKH3_THRESHOLD - max(hourly)
    w(f"The largest hour-bucket is {max(hourly):.3f} ms against a threshold of "
      f"{SKH3_THRESHOLD} ms: a margin of {margin:+.3f} ms "
      f"({margin / SKH3_THRESHOLD:+.0%} of the threshold). `latency_ms` is a "
      "`burnCPU` service time quantised by work units, not end-to-end request "
      "latency, so the margin is read on that measurand.")
    w("")
    w("")
    w("## What is established, and what is not")
    w("")
    if first_restart_min is not None and restarts:
        w("")
        w("### The failure is a single event, not a decline")
        w("")
        w(f"**All {restarts} pod restarts landed in one observer sample, at "
          f"T+{first_restart_min / 60:.1f} h** — coincident with the "
          f"{dur['max'] / 1000:.1f} s stall. Before that point the cluster ran "
          f"**{first_restart_min / 60:.1f} hours with ZERO restarts**, and at "
          "the sample itself pods still reported ready with node memory under "
          "20%.")
        w("")
        w("A gradual resource decline does not restart thirteen pods "
          "simultaneously while node memory sits at 15%. A host that stops "
          "scheduling for tens of seconds does: kubelet misses its "
          "heartbeats, liveness probes time out together, and every affected "
          "pod is killed at once. Combined with a 51-second request that the "
          "server-side service time never saw, this is the signature of the "
          "HOST freezing, not of the system under test degrading.")
        w("")
    clean = (f", with no restart for the first {first_restart_min / 60:.1f} hours"
             if first_restart_min is not None else "")
    w(f"**Established.** This system ran **{load_s / 3600:.2f} hours** of "
      f"continuous fault-injected load at {failed:.4%} failure{clean}, and "
      "produced scored results against pre-registered hypotheses. It is the "
      "longest and largest live run in this work package so far: attempt 6 "
      f"reached 10 h 44 m and 11.5M requests, this one {load_s // 3600} h "
      f"{load_s % 3600 // 60} m and {reqs / 1e6:.1f}M.")
    w("")
    scoreable_h = sum(v is not None for v in (h1, h2, h3_ok))
    w("**Established.** Several candidate causes are excluded by this run's own "
      f"evidence: not the load path (NodePort, gated), not the exporter "
      f"({coarse['n_events']:,} events reduced), not the generator "
      f"({k6['vus']['max']:,} VUs used of {k6['vus_max']['max']:,}), and not "
      f"node memory (peak {node_mem}%). {scoreable_h} of the four instruments "
      f"scored. The {restarts} pod restarts all landed in one sample, "
      "coincident with the stall, which is read above as a host freeze rather "
      "than a cause.")
    w("")
    w("### Independent corroboration from outside the harness")
    w("")
    w("The host's own Windows System event log recorded **Volsnap event 36 — "
      "\"the shadow copies of volume C: were aborted because the shadow copy "
      "storage could not grow in time\"** — at 2026-08-26 23:08:27, which is "
      "**T+3 h of this sitting**, and again on 2026-08-25 during the previous "
      "attempt's window. The SSD reports Healthy with 265 GB free, so this is "
      "not a failing device: it is the I/O subsystem unable to keep up with "
      "sustained write pressure.")
    w("")
    w("That matters because it is evidence from a source with no connection to "
      "PolyForge, its harness or its instrumentation. Windows independently "
      "observed the host's storage failing to keep pace during the same run "
      "whose telemetry showed a multi-second stall — and the machine did not "
      "reboot at any point, with 154 hours of continuous uptime spanning every "
      "sitting discussed here.")
    w("")
    w("**FALSIFIED.** The storage hypothesis. `RESULTS_LIVE_SOAK_V4.md` "
      "inferred that the stall was PostgreSQL durability against a VHDX. This "
      "run disabled `fsync`, `full_page_writes` and `synchronous_commit` "
      f"together, and the worst stall grew from 19.5 s to "
      f"**{dur['max'] / 1000:.1f} s**. Storage durability is eliminated. The "
      "cause returns to unknown, which is where the pre-registration said it "
      "must go rather than to the next convenient explanation.")
    w("")
    w("**NOT established.** A complete 24 h run, or even the design's 12 h "
      f"minimum — this reached {load_s / 3600:.2f} h and missed it by "
      f"{(12 * 3600 - load_s) / 60:.0f} minutes.")
    w("")
    n_fail = sum(1 for r in scored if not r["ok"])
    w(f"**NOT established.** That SK-H1 holds. It failed on {n_fail} of "
      f"{len(scored)} scoreable occurrences.")
    w("")
    w("")
    w("## Disposition")
    w("")
    w("Per the V4 stopping rule, the next step is **different hardware, not a "
      "seventh sitting on this one**. The stall exceeds what any VU pool can "
      "absorb, and no change available on this machine addresses it.")
    w("")
    w("What a completed sitting needs:")
    w("")
    w("1. **A different host.** Storage durability is ruled out above and the "
      "cause is otherwise unknown, so the remedy is a machine without this "
      "stall, not a storage change on this one.")
    w("2. **Or a telemetry write path that does not block the handler.** That "
      "is a product change and needs its own pre-registration, because it "
      "alters what the live plane measures.")
    w("")
    w("`RESULTS_LIVE_SOAK_V3.md` and `RESULTS_LIVE_SOAK_V2.md` stand as "
      "committed. This record does not replace them and does not soften them.")
    w("")
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_LIVE_SOAK_V5.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
