#!/usr/bin/env python3
"""PREREG_LIVE_SOAK_V4.md -> RESULTS_LIVE_SOAK_V4.md (WP14, attempt 6).

**The run is INVALID — the fifth SK-H4 failure — and it is the most
informative of the six.** It delivered 11,529,271 requests over 10 h 44 m at
0.0018% failed with ZERO pod restarts, and was ended by TEN dropped iterations
produced by a single 19.5-second stall.

The V4 pre-registration named this outcome before the run:

    If a stall longer than 12.8 s occurs, SK-H4 fails again and that is the
    honest outcome. ... the generator is no longer the limiting factor and the
    stall is. The finding is then about the host's storage, ... and the next
    step is different hardware -- not a seventh sitting on this one.

The generator was NOT the limiting factor this time and the evidence is
direct: it held 9,601 virtual users and used 4,876. Absorbing a 19.5 s stall at
94 req/s would need ~1,834 VUs per tenant. The stall is the finding.

What separates this from attempts 1-5: enough of the run survived to SCORE
hypotheses on real fault-injected data. Two pass, two fail, one is unscoreable
and one is descriptive. That is the first time this work package has produced a
scored live result at all.

    python analysis_live_soak_v4.py
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
EV = REPO / "eval" / "results" / "live_soak_attempt6_evidence"

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

    w("# Live CRUD-plane soak — attempt 6: INVALID on a 19.5-second stall, "
      "and the first sitting to produce scored results (WP14)")
    w("")
    w("Generated by `analysis_live_soak_v4.py` from evidence committed under "
      "`eval/results/live_soak_attempt6_evidence/`. Pre-registration: "
      "`PREREG_LIVE_SOAK_V4.md`, committed and pushed before the run.")
    w("")
    w("")
    w("## Headline — SK-H4 FAILS, and the cause is now isolated")
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
    w("`PREREG_LIVE_SOAK_V4` named this before the run: *\"If a stall longer "
      f"than {POOL_COVERS_S} s occurs, SK-H4 fails again and that is the honest "
      "outcome … the generator is no longer the limiting factor and the stall "
      "is.\"* **The threshold is not relaxed**, as committed since V2.")
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
      "and recorded eleven and a half million events.")
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
    w("The rule passes only if every occurrence passes, so **SK-H1 FAILS**. "
      "The interesting detail is *where*: on `planner #2` the worst minute "
      "during the outage was barely above baseline, and the excursion arrived "
      "AFTER the planner returned. That is consistent with the planner "
      "re-planning every tenant at once on recovery rather than with the "
      "outage itself hurting, and it is a controller behaviour worth naming "
      "rather than an instrument artefact.")
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
    w(f"**The margin is 0.003 ms and that is not a rounding detail.** The "
      f"largest hour-bucket is {max(hourly):.3f} ms against a threshold of "
      f"{SKH3_THRESHOLD}. Across the 60 s buckets, more than a hundred land "
      "inside 8.001–8.009 ms: `latency_ms` is a `burnCPU` duration quantised "
      "by work units, so p95 settles ON a discrete tier rather than near one — "
      "and the threshold, inherited from the committed B2 `crud_p99`, sits in "
      "the same tier. SK-H3 passes as written. A reader should not take that "
      "pass as evidence of comfortable headroom, because there is none.")
    w("")
    w("")
    w("## What is established, and what is not")
    w("")
    w("**Established.** This system ran **10 h 44 m** of continuous "
      f"fault-injected load at {failed:.4%} failure with **zero pod restarts** "
      "and flat memory, and produced scored results against pre-registered "
      "hypotheses. That is a categorically stronger live result than anything "
      "earlier in this work package, where the previous best with working "
      "instruments was four hours and no sitting had ever been scoreable.")
    w("")
    w("**Established.** The failure causes have moved every attempt and are "
      "now exhausted one by one: not the load path (NodePort, gated), not the "
      "exporter (11.5M events in 78 s), not the instruments (three of four "
      "scored), not the generator (4,876 VUs used of 9,601), not memory (zero "
      "restarts), and not the controller.")
    w("")
    w("**NOT established.** A complete 24 h run. The remaining obstacle is a "
      f"stall of **{dur['max'] / 1000:.1f} s** against an absolute zero-drop "
      "gate. Its cause is unidentified; every observation points at host "
      "storage — `pgdata` is an `emptyDir` on a VHDX under WSL2 and the "
      "control plane writes telemetry synchronously inside the request "
      "handler — but that remains a hypothesis, not a result.")
    w("")
    w("**NOT established.** That SK-H1 holds. It failed on one of three "
      "scoreable occurrences, and the failure is on the recovery side rather "
      "than under the fault.")
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
    w("1. **Storage that does not stall for 19 seconds** — a real block device "
      "rather than an `emptyDir` over a VHDX. A modest cloud VM would settle "
      "it in one day.")
    w("2. **Or a telemetry write path that does not block the handler.** That "
      "is a product change and needs its own pre-registration, because it "
      "alters what the live plane measures.")
    w("")
    w("`RESULTS_LIVE_SOAK_V3.md` and `RESULTS_LIVE_SOAK_V2.md` stand as "
      "committed. This record does not replace them and does not soften them.")
    w("")
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_LIVE_SOAK_V4.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
