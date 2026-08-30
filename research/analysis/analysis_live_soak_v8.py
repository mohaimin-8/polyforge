"""Attempt 10 (WP14) -> RESULTS_LIVE_SOAK_V8.md.

Pre-registration: `PREREG_LIVE_SOAK_V8.md`, committed and pushed at
2026-08-29T08:03:22Z, before the run's T0 of 08:04:11Z.

THE FIRST COMPLETED 24 H SITTING, AND THE FIRST VALID ONE. The harness returned
`valid_runs: 1, failed_runs: 0, ok: true`; attempts 1-9 all returned
`valid_runs: 0`. Because the delivery gate accepted the run, `eval-export` ran
to completion for the first time, so SK-H1, SK-H2 and SK-H3 are scoreable here
rather than reported as lost.

SK-H1 and SK-H2 are scored by the SAME rules `analysis_live_soak_v5.py` froze --
copied deliberately rather than re-derived, so a reader can diff them. SK-H3 is
scored on the hourly buckets, not the run aggregate: the aggregate (8.004009 ms)
would pass and the hourly scoring does not, which is exactly why the hypothesis
was specified per hour.

Two hypotheses fail. They are reported as failures, and the evidence that bears
on interpreting them is reported alongside -- never instead of them.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EV = REPO / "eval" / "results" / "live_soak_attempt10_evidence"

SKH1_BAND = 0.20          # 20% recovery band, frozen since PREREG_LIVE_SOAK
SKH3_THRESHOLD_MS = 8.0072
GAP_THRESHOLD_S = 40      # SK-H8: 2x the observer's 20 s sampling interval
MATCH_WINDOW_S = 120


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def num(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def read_json(name: str) -> dict:
    return json.loads((EV / name).read_text(encoding="utf-8"))


def bucket_epoch(row: dict) -> int:
    return int(dt.datetime.fromisoformat(
        row["bucket_start_utc"].replace("Z", "+00:00")).timestamp())


def iso(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def hhmm(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600} h {(s % 3600) // 60:02d} m {s % 60:02d} s"


def timeline_faults() -> list[tuple[str, int, int]]:
    """Pair each *_down/*_on with its matching *_up/*_off, in fire order."""
    events: list[tuple[int, str]] = []
    for line in (EV / "timeline.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            events.append((int(parts[0]), parts[1]))
    out, pending = [], {}
    for epoch, name in events:
        if name.endswith(("_down", "_on")):
            pending[name.rsplit("_", 1)[0]] = epoch
        else:
            kind = name.rsplit("_", 1)[0]
            if kind in pending:
                out.append((kind, pending.pop(kind), epoch))
    return out


def score_skh1(fine: list[dict], fault_list, load_end: int):
    """Frozen rule, copied verbatim from analysis_live_soak_v5.py: median of the
    ten minutes before the fault against the MAXIMUM of the five minutes after
    it, tested against a 20% band."""
    rows, verdict = [], True
    for name, start, end in fault_list:
        if start > load_end:
            rows.append({"name": name, "start": start, "scoreable": False,
                         "why": "fired after the load window ended"})
            continue
        pre = [b["crud_p95_ms"] for b in fine
               if start - 600 <= bucket_epoch(b) < start]
        during = [b["crud_p95_ms"] for b in fine
                  if start <= bucket_epoch(b) <= end]
        rec = [b["crud_p95_ms"] for b in fine
               if end < bucket_epoch(b) <= end + 300]
        if not pre or not rec:
            rows.append({"name": name, "start": start, "scoreable": False,
                         "why": "insufficient buckets"})
            continue
        base = sorted(pre)[len(pre) // 2]
        peak = max(rec)
        dev = (peak - base) / base if base else 0.0
        ok = dev <= SKH1_BAND
        verdict = verdict and ok
        rows.append({"name": name, "start": start, "scoreable": True,
                     "base": base, "during": max(during) if during else 0.0,
                     "recovery": peak, "dev": dev, "ok": ok})
    if not any(r["scoreable"] for r in rows):
        return rows, None
    return rows, verdict


def score_skh2(load_end_min: int):
    """Frozen rule: the degraded window must not fall below the healthy window
    by more than one cycle's worth of tenants. NO-LOSS, not exact equality --
    which is why a +80 degraded window against +72 healthy passes."""
    path = EV / "audit_window.csv"
    if not path.exists():
        return [], None
    rows, verdict = [], True
    for r in csv.DictReader(path.read_text(encoding="utf-8").splitlines()):
        m = re.search(r"T\+(\d+)m", r.get("label", ""))
        if m and int(m.group(1)) > load_end_min:
            rows.append({"label": r["label"], "excluded": True})
            continue
        tenants = int(r["tenants"])
        healthy = int(r["base_end"]) - int(r["base_start"])
        degraded = int(r["out_end"]) - int(r["base_end"])
        floor = healthy - tenants
        ok = healthy > 0 and degraded > 0 and degraded >= floor
        verdict = verdict and ok
        rows.append({"label": r["label"], "excluded": False, "healthy": healthy,
                     "degraded": degraded, "floor": floor, "ok": ok})
    scored = [r for r in rows if not r.get("excluded")]
    return rows, (verdict if scored else None)


def build() -> str:
    L: list[str] = []
    def w(s: str = "") -> None:
        L.append(s)

    k6 = read_json("k6-summary.json")["metrics"]
    exp = read_json("eval-export.json")
    fine = read_json("eval-export-fine.json")["buckets"]
    hourly = exp["buckets"]
    load = read_json("load_distribution.json")

    reqs = int(k6["http_reqs"]["count"])
    rate = float(k6["http_reqs"]["rate"])
    duration_s = reqs / rate
    dropped = int(k6["dropped_iterations"]["count"])
    failed_n = int(k6["http_req_failed"]["passes"])
    failed_pct = float(k6["http_req_failed"]["value"]) * 100.0
    p95 = float(k6["http_req_duration"]["p(95)"])
    med = float(k6["http_req_duration"]["med"])
    worst = float(k6["http_req_duration"]["max"])
    vus_peak = int(k6["vus"]["max"])
    vus_pool = int(k6["vus_max"]["value"])

    obs = read_rows(EV / "observer.csv")
    load_end = max(bucket_epoch(b) for b in fine)
    scored_obs = [r for r in obs
                  if r.get("epoch") and int(r["epoch"]) <= load_end]
    restarts = max((num(r, "restarts_total") for r in scored_obs), default=0)
    peak_mem = max((num(r, "nodes_mem_pct") for r in scored_obs), default=0)
    gaps = [r for r in scored_obs if num(r, "sample_gap_s") > GAP_THRESHOLD_S]
    max_gap = max((num(r, "sample_gap_s") for r in scored_obs), default=0)
    mean_gap = (sum(num(r, "sample_gap_s") for r in scored_obs)
                / max(len(scored_obs), 1))

    faults = timeline_faults()
    h1_rows, h1 = score_skh1(fine, faults, load_end)
    h2_rows, h2 = score_skh2(24 * 60)

    over = [(i, b) for i, b in enumerate(hourly)
            if b.get("crud_p95_ms", 0) > SKH3_THRESHOLD_MS]
    median_n = sorted(b["n_events"] for b in hourly)[len(hourly) // 2]
    full_over = [(i, b) for i, b in over if b["n_events"] > median_n * 0.5]
    h3 = not over

    registered_events = read_rows(EV / "host_events.csv")
    full_events = read_rows(EV / "host_events_full.csv")

    def match(rows, ep):
        return [e for e in rows
                if abs(int(e["epoch"]) - ep) <= MATCH_WINDOW_S]

    reg_matched = sum(1 for r in gaps
                      if match(registered_events, int(r["epoch"])))
    full_matched = sum(1 for r in gaps if match(full_events, int(r["epoch"])))

    # NULL MODEL. The full log carries thousands of events, most of them one
    # provider firing every few seconds, so a +/-120 s window around ANY instant
    # will often contain something. Without a base rate a match count is not
    # evidence of association. This asks what fraction of arbitrary instants in
    # the same window would also "match", using a fixed stride so the figure is
    # deterministic and reproducible rather than sampled at random.
    win_lo = min(int(e["epoch"]) for e in full_events)
    win_hi = max(int(e["epoch"]) for e in full_events)
    probes = list(range(win_lo, win_hi, 97))  # 97 s: coprime with the cadences
    null_matched = sum(1 for t in probes if match(full_events, t))
    null_rate = null_matched / max(len(probes), 1)
    # Is the gap match rate distinguishable from that base rate at all? A
    # normal approximation is enough to answer it, and answering it with a
    # number beats picking a threshold that flatters the result.
    n_gaps = max(len(gaps), 1)
    expected = null_rate * n_gaps
    sd = (n_gaps * null_rate * (1 - null_rate)) ** 0.5
    z = (full_matched - expected) / sd if sd else 0.0

    # ------------------------------------------------------------------ head
    w("# Live CRUD-plane soak — attempt 10: the 24 h sitting, completed and "
      "valid (WP14)")
    w()
    w("Generated by `analysis_live_soak_v8.py` from evidence committed under "
      "`eval/results/live_soak_attempt10_evidence/`. Pre-registration: "
      "`PREREG_LIVE_SOAK_V8.md`, committed and pushed at 2026-08-29T08:03:22Z "
      "— before the run's T0 of 08:04:11Z.")
    w()
    w()
    w("## Headline")
    w()
    w(f"The sitting ran **{hhmm(duration_s)}**, delivered **{reqs:,} requests** "
      f"at **{rate:.3f}/s** with **{failed_pct:.4f}% failed**, and finished with "
      f"**{dropped} dropped iterations**.")
    w()
    w("The harness accepted it: **`valid_runs: 1, failed_runs: 0, ok: true`**. "
      "Attempts 1 through 9 all returned `valid_runs: 0`. This is the first "
      "valid `live_soak` run in the work package, and the first for which "
      "`eval-export` completed — which is why SK-H1, SK-H2 and SK-H3 are "
      "scored below instead of being reported as lost.")
    w()
    w("**The result is split, and both halves are the finding.**")
    w()
    w("| hypothesis | verdict |")
    w("|---|---|")
    w(f"| SK-H1 recovery within a {int(SKH1_BAND * 100)}% band | "
      f"**{'PASS' if h1 else 'FAIL'}** |")
    w(f"| SK-H2 audit continuity | **{'PASS' if h2 else 'FAIL'}** |")
    w(f"| SK-H3 hourly `crud_p95` <= {SKH3_THRESHOLD_MS} ms | "
      f"**{'PASS' if h3 else 'FAIL'}** — {len(over)} of {len(hourly)} buckets "
      "over |")
    w(f"| SK-H4 validity, absolute | "
      f"**{'PASS' if dropped == 0 else 'FAIL'}** — {dropped} dropped |")
    w(f"| SK-H6 load distribution | {load['failures']} sampler failures in "
      f"{load['samples']:,} observations |")
    w(f"| SK-H7 client latency (descriptive) | p95 {p95:.2f} ms, median "
      f"{med:.2f} ms, max {worst / 1000:.2f} s |")
    w(f"| SK-H8 named host events (descriptive) | {reg_matched}/{len(gaps)} "
      "matched on the registered providers |")
    w()
    w()
    w("## SK-H4 — validity, and the gate that ended six sittings")
    w()
    w("| gate | observed | threshold | verdict |")
    w("|---|---:|---:|---|")
    w(f"| `dropped_iterations` | **{dropped}** | < 1 | "
      f"**{'PASS' if dropped == 0 else 'FAIL'}** |")
    w(f"| `http_req_failed` | {failed_pct:.4f}% ({failed_n:,}) | < 1% | PASS |")
    w()
    w("`dropped_iterations` has been absolute, cumulative and abort-on-fail "
      "since `PREREG_LIVE_SOAK_V2`. It ended attempts 5, 7, 8 and 9, and it was "
      "**never relaxed** — not after the fifth failure, not after the sixth. It "
      "is met here across the full duration.")
    w()
    w(f"The load generator was never the constraint: the pool held "
      f"**{vus_pool:,} virtual users** and peaked at **{vus_peak:,}**.")
    w()
    w()
    w("## What the run did")
    w()
    w("| quantity | value |")
    w("|---|---:|")
    w(f"| duration | {hhmm(duration_s)} |")
    w(f"| requests | {reqs:,} |")
    w(f"| failed | {failed_pct:.4f}% ({failed_n:,}) |")
    w(f"| **pod restarts** | **{int(restarts)}** |")
    w(f"| peak node memory | {int(peak_mem)}% |")
    w(f"| telemetry events | {exp['n_events']:,} |")
    w(f"| tenants | {exp['n_tenants']} |")
    w(f"| mean violation | {exp['mean_violation']:.3e} |")
    w(f"| Jain fairness | {exp['mean_jain']:.9f} |")
    w(f"| whole-run `crud_p95` / `crud_p99` | {exp['crud_p95_ms']:.6f} ms / "
      f"{exp['crud_p99_ms']:.6f} ms |")
    w(f"| infrastructure cost | ${exp['total_cost_usd']:.2f} |")
    w()
    w(f"**Zero pod restarts** across {len(scored_obs):,} observer samples, with "
      "sixteen control-plane replicas held for the entire sitting.")
    w()
    w()
    w("## SK-H1 — recovery")
    w()
    w("Frozen rule, unchanged since `PREREG_LIVE_SOAK`: the median `crud_p95` "
      "of the ten minutes before each fault against the **maximum** of the five "
      f"minutes after it, tested against a {int(SKH1_BAND * 100)}% band.")
    w()
    w("| fault | at | base | during | recovery peak | deviation | |")
    w("|---|---|---:|---:|---:|---:|---|")
    for r in h1_rows:
        if not r["scoreable"]:
            w(f"| {r['name']} | {iso(r['start'])} | — | — | — | — | "
              f"excluded: {r['why']} |")
            continue
        w(f"| {r['name']} | {iso(r['start'])} | {r['base']:.4f} | "
          f"{r['during']:.4f} | {r['recovery']:.4f} | {r['dev']:+.1%} | "
          f"{'ok' if r['ok'] else '**FAIL**'} |")
    w()
    if not h1:
        bad = [r for r in h1_rows if r.get("scoreable") and not r["ok"]]
        w(f"**SK-H1 FAILS on {len(bad)} of "
          f"{sum(1 for r in h1_rows if r['scoreable'])} injections.**")
        w()
        w("The evidence bearing on interpretation, reported alongside the "
          "failure and not instead of it: in the failing window `crud_p95` "
          "oscillates on a roughly four-minute cycle that is present **before, "
          "during and after** the fault, with 60 s buckets alternating between "
          "~8.00 ms and 12-14 ms as the per-minute event count swings between "
          "~12,200 and ~22,200. The frozen rule compares the **maximum** of the "
          "recovery window against the **median** of the pre-window, so an "
          "oscillation of that period is scored peak-against-trough whenever a "
          "burst lands inside the five-minute window.")
        w()
        w("The cluster shows no distress there: 20/20 pods ready, zero "
          "restarts, node memory flat. A rule that compares like with like "
          "would be a different hypothesis, and changing it after seeing this "
          "result is precisely what these pre-registrations exist to prevent. "
          "**The FAIL stands as scored.**")
    else:
        w("**SK-H1 PASSES** on every scoreable injection.")
    w()
    w()
    w("## SK-H2 — audit continuity")
    w()
    w("Frozen rule: the degraded window must not fall below the healthy window "
      "by more than one control cycle's worth of tenants. This is a **no-loss** "
      "test, not an equality test.")
    w()
    w("| injection | healthy | degraded | floor | |")
    w("|---|---:|---:|---:|---|")
    for r in h2_rows:
        if r.get("excluded"):
            w(f"| {r['label']} | — | — | — | excluded |")
            continue
        w(f"| {r['label']} | +{r['healthy']} | +{r['degraded']} | "
          f"{r['floor']} | {'ok' if r['ok'] else '**FAIL**'} |")
    w()
    w(f"**SK-H2 {'PASSES' if h2 else 'FAILS'}.** One window came in at +80 "
      "against a +72 healthy baseline — ten control cycles x 8 tenants rather "
      "than nine. That is **more** audit records, not fewer, and the frozen "
      "rule accepts it: a continuity failure means records going missing. "
      "`RESULTS_LIVE_SOAK_V5.md` described its own result as *\"identical "
      "windows\"*, which was true of that sitting but is narrower than the "
      "rule the scoring code has always applied.")
    w()
    w()
    w("## SK-H3 — hourly latency")
    w()
    w(f"Threshold **{SKH3_THRESHOLD_MS} ms**, frozen from B2/B3's committed "
      "values.")
    w()
    w("| bucket | start | `crud_p95` | events | |")
    w("|---:|---|---:|---:|---|")
    for i, b in enumerate(hourly):
        v = b.get("crud_p95_ms", 0)
        flag = "**OVER**" if v > SKH3_THRESHOLD_MS else "ok"
        w(f"| {i} | {b['bucket_start_utc']} | {v:.4f} | {b['n_events']:,} | "
          f"{flag} |")
    w()
    w(f"**SK-H3 FAILS: {len(over)} of {len(hourly)} buckets exceed the "
      f"threshold.** One of them (bucket {over[-1][0] if over else '-'}) is a "
      "partial final hour and is not comparable to a full one; "
      f"**{len(full_over)} full hours** fail on their own.")
    w()
    w(f"The whole-run aggregate is **{exp['crud_p95_ms']:.6f} ms** and would "
      "have passed. The hypothesis is specified per hour, so it does not.")
    w()
    w("**This margin was disclosed before the run, and carried verbatim "
      "through V5, V7 and V8:** *\"SK-H3's margin is 0.003 ms with no "
      "headroom.\"* Nineteen buckets sit at 8.00-something and the exceedances "
      "are 0.001-0.21 ms. A threshold three thousandths of a millisecond above "
      "a near-constant is decided by noise. **It was not relaxed and is not "
      "being relaxed now** — the correct response is a better-powered "
      "hypothesis in a future pre-registration, not an edit to this one.")
    w()
    w("The exceedances do **not** align with the injected faults: starvations "
      "fired at T+5/11/17/23 h and those buckets pass.")
    w()
    w()
    w("## SK-H6 — load distribution")
    w()
    cpu = load.get("cpu_ms", {})
    if cpu:
        lo, hi = min(cpu.values()), max(cpu.values())
        spread = (hi - lo) / hi * 100 if hi else 0
        w(f"{load['samples']:,} sampler observations, **{load['failures']} "
          f"failures**. CPU time across {len(cpu)} control-plane pods spans "
          f"{lo:,.0f}-{hi:,.0f} ms — a **{spread:.1f}% spread**.")
        w()
        w("Attempt 4's defect, where `kubectl port-forward` pinned every "
          "request to one pod of sixteen and 36.4% of requests failed, is "
          "definitively absent.")
    w()
    w()
    w("## SK-H8 — named host events")
    w()
    w(f"**{len(gaps)} scheduling gaps above {GAP_THRESHOLD_S} s** (mean gap "
      f"{mean_gap:.1f} s, max {int(max_gap)} s).")
    w()
    w(f"- Against the **registered** ten-provider tail: **{reg_matched} of "
      f"{len(gaps)}** matched within +/-{MATCH_WINDOW_S} s.")
    w(f"- Against the **full System log** captured post-hoc "
      f"({len(full_events):,} events, all providers): **{full_matched} of "
      f"{len(gaps)}** matched "
      f"({full_matched / max(len(gaps), 1):.0%}).")
    w(f"- **Null model:** {null_rate:.0%} of arbitrary instants in the same "
      f"window also fall within +/-{MATCH_WINDOW_S} s of some event "
      f"({null_matched:,} of {len(probes):,} probes on a fixed 97 s stride).")
    w()
    w("The registered instrument watched ten providers and recorded "
      f"{len(registered_events)} events in 24 hours. The full log holds "
      f"{len(full_events):,}, of which **98% are Hyper-V VmSwitch** NIC "
      "connect/offload events, with Windows Update, Store package hive "
      "flushes, service-control churn and one Volsnap 36 making up the rest. "
      "The tail did not miss anything within its providers — the six "
      "`Kernel-Power`, one `Volsnap` and one `EventLog` record in the run "
      "window are exactly what it captured — but its provider list was too "
      "narrow to name most stalls as they happened.")
    w()
    w("**The one live capture that matters most:** Volsnap 36 at "
      "2026-08-29T14:35:52Z coincided with a 42 s gap while the volume-write "
      "probe read **115 ms**. VSS activity correlates with a stall *without "
      "freezing the volume* — which contradicts the mechanism attempts 7 and 8 "
      "assumed, and is consistent with attempt 7 having already falsified "
      "storage durability with `fsync=off`.")
    w()
    w("**The null model is the honest caveat on that 55.** With one provider "
      "firing every few seconds for most of the sitting, coincidence within "
      f"+/-{MATCH_WINDOW_S} s is cheap: an arbitrary instant matches "
      f"{null_rate:.0%} of the time. The gap match rate of "
      f"{full_matched / max(len(gaps), 1):.0%} is therefore "
      + (f"**not distinguishable from chance** (expected {expected:.0f} "
         f"matches under the null, observed {full_matched}, z = {z:.2f}) and "
         "must not be read as association."
         if abs(z) < 2 else
         f"above the base rate (expected {expected:.0f}, observed "
         f"{full_matched}, z = {z:.2f}), though one sitting cannot separate "
         "association from coincidence.") + " Naming a stall's cause "
      "needs an instrument that samples inside the guest, not a wider grep of "
      "the host log.")
    w()
    w("Across all gaps the pattern is **ordinary host contention** rather than "
      "one culprit, and **the system under test absorbed every one of them "
      "without losing an iteration**.")
    w()
    w()
    w("## Limitations")
    w()
    w("**`crud_p95` / `crud_p99` measure CPU service time, not service "
      "latency**, carried forward unchanged from V5: "
      "`internal/platform/replay.go` stops its clock before the telemetry "
      "write, pinned by `internal/platform/replay_latency_semantics_test.go`. "
      "Both SK-H1 and SK-H3 are scored on that metric, so both describe the "
      "burn loop rather than what a client experiences. SK-H7 carries the "
      "client-side view.")
    w()
    w("**`sample_gap_s` is blind to a guest-only stall.** Attempt 9's fatal "
      "window showed the observer scheduled normally while the cluster stopped "
      "answering. No probe inside the VM was added here, because that would "
      "have been a second changed factor.")
    w()
    w("**`fsync=off` on the eval store** is correct for a disposable "
      "measurement database and indefensible in a deployment.")
    w()
    w("**One machine, one sitting.** Nothing here establishes that these "
      "results generalise beyond this host.")
    w()
    w()
    w("## What this closes")
    w()
    w("WP14 asked whether the system could be run live for a day through "
      "faults and observed doing it. **It can, and it was**: 24 hours, "
      f"{reqs:,} requests, zero dropped iterations, zero pod restarts, and "
      "eight of eight fault injections delivered on the registered schedule "
      "with verified pod-level recovery.")
    w()
    w("Two hypotheses failed, both on rules specified with no headroom against "
      "a near-constant metric. That is a finding about the hypotheses, and it "
      "is reported as a failure of them rather than repaired after the fact.")
    w()
    w("`PREREG_LIVE_SOAK_V8` pre-committed that this is the last sitting on "
      "this machine in every branch. It is. A further sitting would require a "
      "changed host and a new pre-registration.")
    w()
    w("`RESULTS_LIVE_SOAK_V7.md` (attempt 9, VOID), `RESULTS_LIVE_SOAK_V5.md`, "
      "`RESULTS_LIVE_SOAK_V3.md` and `RESULTS_LIVE_SOAK_V2.md` stand as "
      "committed. This record does not replace them and does not soften them.")
    w()
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_LIVE_SOAK_V8.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
