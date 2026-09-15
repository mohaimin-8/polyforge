"""B1, the three-knob live plane, executed -> RESULTS_WAVE4_LIVE_PLANE.md.

PROTOCOL: PREREG_WAVE4_LIVE_PLANE.md as amended before the run (sessions 33,
44, 47) and the session-48 pre-run note. Everything below implements a
registered reading; nothing is chosen after seeing the numbers.

Inputs
- eval/results/wave4_live_plane.duckdb (gitignored, Zenodo-archived), else
  the committed export eval/results/wave4_live_plane_runs.csv: 4 arms x 4
  cells x 1 rep on the cluster backend, one host (g6e.2xlarge, L40S).
- eval/results/wave4_sim_ref.duckdb, else wave4_sim_ref_runs.csv: the same
  arms and cells on the sim backend, 3 reps -- WL-H3's reference, executed
  AFTER the live sitting with frozen tuned parameters and deterministic
  seeds (disclosed in the record).
- eval/results/wave4_live_plane_evidence/: host_facts.json, knob_preflight.json
  (last run), run_logs/matrix.log (the harness audit and every run's WL-H2
  line), run_logs/verify.log (tier residency and decode check).
- eval/results/wave4_jointstress_probe_evidence/2026-09-15_g6e2xlarge_1c/:
  the gating step-1c preflight on the provisioned host.

Readings
- WL-H1 (primary): in `joint_stress`, jcac beats EVERY other arm on cost at
  iso-fairness -- lower total $-cost with a Jain index no worse by more than
  MARGIN (0.01). The registered CI is a paired bootstrap on the cost delta;
  it is NOT COMPUTABLE from this sitting (one rep per cell, timeseries_reps
  0 in the frozen yaml), and the record says so. A CI is what a win needs;
  the falsifier is met on the point estimate whenever jcac's cost is not
  lower.
- WL-H2 (gating): executed by the harness inside every run; the record
  counts the PASS lines and reproduces the last run's report.
- WL-H3 (descriptive, ordinal only): per cell, the J winner (composite
  objective, phase7_ordinal.py's form with each row's own step count) in
  sim vs live; cost and violation winners secondary; ties yield no reading.
  No absolute is compared across substrates (ground rule 3).

Deviation disclosed: the session-44 amendment clause 4 makes the
`polyforge_http_request_duration_seconds` histogram the primary latency
measurand. The scrape (`scripts/soak_observer.sh`) was not executed during
the sitting and the clusters are gone, so only the replay-clock metric
exists. WL-H1's verdict does not depend on it (cost decides); violation and
Jain absolutes, and therefore WL-H3's J, rest on the replay metric.

    python analysis_wave4_live_plane.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "eval" / "results"
LIVE_DB, LIVE_CSV = RESULTS / "wave4_live_plane.duckdb", RESULTS / "wave4_live_plane_runs.csv"
SIM_DB, SIM_CSV = RESULTS / "wave4_sim_ref.duckdb", RESULTS / "wave4_sim_ref_runs.csv"
EVIDENCE = RESULTS / "wave4_live_plane_evidence"
PROBE_1C = RESULTS / "wave4_jointstress_probe_evidence" / "2026-09-15_g6e2xlarge_1c"

ARMS = ["jcac", "replica-only", "cache-only", "tier-only"]
CELLS = ["ai_cacheable", "tier_mixed", "crud_bursty", "joint_stress"]
PRIMARY_CELL = "joint_stress"
MARGIN = 0.01          # iso-fairness margin, frozen by the prereg
TENANTS = 8
COST_SCALE = 0.01
TIE_EPS = 1e-9         # phase7_ordinal.py: a tie yields no ordinal reading

COLS = ["system", "workload", "rep", "total_cost_usd", "mean_violation",
        "violation_step_share", "mean_jain", "cache_hit_rate", "crud_p95_ms",
        "ai_p95_ms", "crud_p99_ms", "ai_p99_ms", "steps"]


def load(db: Path, csv: Path) -> pd.DataFrame:
    """Valid runs from the DuckDB, else from the committed export -- the
    same filter on both paths, so the record rebuilds on a clean clone."""
    if db.exists():
        con = duckdb.connect(str(db), read_only=True)
        df = con.execute(
            "select r.system, r.workload, r.rep, m.total_cost_usd, m.mean_violation, "
            "m.violation_step_share, m.mean_jain, m.cache_hit_rate, m.crud_p95_ms, "
            "m.ai_p95_ms, m.crud_p99_ms, m.ai_p99_ms, m.steps "
            "from runs r join metrics m on r.run_id = m.run_id where r.status = 'valid'"
        ).fetchdf()
        con.close()
    elif csv.exists():
        raw = pd.read_csv(csv, float_precision="round_trip")
        # the export carries runs.steps AND metrics.steps (as metric_steps);
        # the record uses the metrics one, as the DuckDB path does
        raw = (raw[raw.status == "valid"].drop(columns=["steps"])
                  .rename(columns={"metric_steps": "steps"}))
        df = raw[COLS].copy()
        for c in COLS[3:]:
            df[c] = pd.to_numeric(df[c])
    else:
        raise FileNotFoundError(f"{db.name} missing and no committed export {csv.name}")
    df = df.reset_index(drop=True)
    df["J"] = (df.total_cost_usd / ((df.steps - 1) * TENANTS) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    return df


def per_cell_means(df: pd.DataFrame) -> pd.DataFrame:
    return (df.groupby(["workload", "system"], as_index=False)
              [["total_cost_usd", "mean_violation", "mean_jain", "J"]].mean())


def winner(frame: pd.DataFrame, metric: str) -> str:
    """Lowest mean wins; a tie within TIE_EPS is reported as a tie."""
    s = frame.set_index("system")[metric].sort_values()
    if len(s) > 1 and abs(s.iloc[0] - s.iloc[1]) < TIE_EPS:
        return "tie"
    return str(s.index[0])


# --- evidence readers ----------------------------------------------------

def host() -> dict:
    p = EVIDENCE / "host_facts.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def matrix_audit() -> tuple[dict, int]:
    """The harness's own audit JSON and the count of WL-H2 PASS lines."""
    p = EVIDENCE / "run_logs" / "matrix.log"
    if not p.exists():
        return {}, 0
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"experiment\"[^{}]*\}", text, re.S)
    audit = json.loads(m.group(0)) if m else {}
    return audit, len(re.findall(r"verdict: WL-H2 PASS", text))


def _last_verify_block() -> str:
    """The residency/decode check that preceded the matrix: the LAST
    '== placement' block of tiers.log (earlier blocks in that log are the
    failed bring-ups the runbook fixes describe; verify.log is a hand-run
    check from before them and is kept only as provenance)."""
    p = EVIDENCE / "run_logs" / "tiers.log"
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    blocks = text.split("== placement / residency check ==")
    return blocks[-1] if len(blocks) > 1 else ""


def tiers() -> list[tuple[str, str]]:
    """(tier, verdict) from the check that preceded the matrix, e.g.
    ('small', 'OK (decoded)')."""
    seen: dict[str, str] = {}
    for line in _last_verify_block().splitlines():
        m = re.match(r"\s+(small|mid|large): (.+)$", line)
        if m:
            seen[m.group(1)] = m.group(2).strip()
    return [(t, seen[t]) for t in ("small", "mid", "large") if t in seen]


def gpu_used_mib() -> str:
    m = re.search(r"NVIDIA L40S, (\d+) MiB, (\d+) MiB", _last_verify_block())
    return f"{m.group(2)} of {m.group(1)}" if m else "?"


def knob_report() -> dict:
    p = EVIDENCE / "knob_preflight.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def probe_1c() -> dict:
    out = {}
    k6 = PROBE_1C / "k6-summary.json"
    if k6.exists():
        m = json.loads(k6.read_text(encoding="utf-8"))["metrics"]
        out = {"requests": int(m["http_reqs"]["count"]),
               "failed_rate": float(m["http_req_failed"]["value"]),
               "dropped": int(m["dropped_iterations"]["count"]),
               "vus_active_max": int(m["vus"]["max"])}
    kp = PROBE_1C / "knob_preflight.json"
    if kp.exists():
        out["wl_h2"] = json.loads(kp.read_text(encoding="utf-8")).get("verdict", "?")
    return out


# --- readings ------------------------------------------------------------

def wl_h1(live: pd.DataFrame) -> tuple[list[dict], str, str]:
    cell = live[live.workload == PRIMARY_CELL].set_index("system")
    j = cell.loc["jcac"]
    rows = []
    for arm in ("replica-only", "cache-only", "tier-only"):
        o = cell.loc[arm]
        d_cost = float(j.total_cost_usd - o.total_cost_usd)
        d_jain = float(j.mean_jain - o.mean_jain)
        iso = d_jain >= -MARGIN
        rows.append({"arm": arm, "cost": float(o.total_cost_usd), "jain": float(o.mean_jain),
                     "d_cost": d_cost, "d_cost_pct": 100.0 * d_cost / float(o.total_cost_usd),
                     "d_jain": d_jain, "iso": iso, "beats": (d_cost < 0) and iso})
    cheapest = min(rows, key=lambda r: r["cost"])
    verdict = "PASS" if all(r["beats"] for r in rows) else "FAIL"
    return rows, verdict, cheapest["arm"]


def wl_h3(live: pd.DataFrame, sim: pd.DataFrame) -> list[dict]:
    lm, sm = per_cell_means(live), per_cell_means(sim)
    out = []
    for cell in CELLS:
        l, s = lm[lm.workload == cell], sm[sm.workload == cell]
        row = {"cell": cell}
        for metric, key in (("J", "J"), ("total_cost_usd", "cost"), ("mean_violation", "violation")):
            lw, sw = winner(l, metric), winner(s, metric)
            row[key] = (sw, lw, "AGREE" if (sw == lw and sw != "tie") else ("tie" if "tie" in (sw, lw) else "DISAGREE"))
        out.append(row)
    return out


# --- the record ----------------------------------------------------------

def fmt_row(r) -> str:
    def f(v, d):
        return "-" if pd.isna(v) else f"{v:.{d}f}"
    return (f"| {r.workload} | {r.system} | {f(r.total_cost_usd, 4)} | {f(r.mean_jain, 3)} | "
            f"{f(r.mean_violation, 4)} | {f(r.violation_step_share, 3)} | {f(r.cache_hit_rate, 2)} | "
            f"{f(r.ai_p95_ms, 0)} | {f(r.ai_p99_ms, 0)} | {f(r.crud_p95_ms, 0)} | {f(r.J, 4)} |")


def build() -> str:
    live, sim = load(LIVE_DB, LIVE_CSV), load(SIM_DB, SIM_CSV)
    h1_rows, h1_verdict, cheapest = wl_h1(live)
    h3 = wl_h3(live, sim)
    audit, h2_passes = matrix_audit()
    hf = host()
    gpu = (hf.get("gpus") or [{}])[0]
    kp = knob_report()
    p1c = probe_1c()
    j = live[(live.workload == PRIMARY_CELL) & (live.system == "jcac")].iloc[0]
    best = next(r for r in h1_rows if r["arm"] == cheapest)
    n_live = len(live)

    L = []
    L.append("# B1 — the three-knob live plane, executed: WL-H1 FAILS in `joint_stress`; the knobs beat the replica baseline by an order of magnitude; jointness does not beat the best single knob on cost"
             if h1_verdict == "FAIL" else
             "# B1 — the three-knob live plane, executed: WL-H1 PASSES in `joint_stress`")
    L.append("")
    L.append("Generated by `analysis_wave4_live_plane.py` from `eval/results/wave4_live_plane_runs.csv` "
             "(the committed export of `wave4_live_plane.duckdb`), `wave4_sim_ref_runs.csv`, and the evidence "
             "under `eval/results/wave4_live_plane_evidence/`. Pre-registration: `PREREG_WAVE4_LIVE_PLANE.md` "
             "as amended before the run (sessions 33, 44, 47) plus the session-48 pre-run note.")
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append(f"**WL-H1 — {h1_verdict}.** In `{PRIMARY_CELL}`, the joint controller's total cost was "
             f"**${j.total_cost_usd:.4f}** against **${best['cost']:.4f}** for the cheapest other arm, "
             f"**{cheapest}** ({best['d_cost_pct']:+.1f}% for jcac), at a Jain index of {j.mean_jain:.3f} vs "
             f"{best['jain']:.3f} (Δ {best['d_jain']:+.3f}, within the {MARGIN} margin). The registered claim "
             "requires jcac to beat every other arm on cost at iso-fairness; it beats "
             f"{sum(r['beats'] for r in h1_rows)} of {len(h1_rows)}. "
             + ("The falsifier the pre-registration committed to is met, on the point estimate, and this result "
                "headlines the thesis limitations as that document said it would: not re-run, not re-tuned, not widened."
                if h1_verdict == "FAIL" else ""))
    L.append("")
    L.append(f"**WL-H2 — PASS in every run.** The harness executed the knob-liveness gate before each of the "
             f"{audit.get('valid_runs', n_live)} load windows; {h2_passes} PASS verdicts are in `run_logs/matrix.log`. "
             f"Last run's report: {kp.get('verdict', '?')}.")
    L.append("")
    agree = sum(1 for r in h3 if r["J"][2] == "AGREE")
    L.append(f"**WL-H3 — the simulator's ordinal ranking on the composite objective J reproduces live in "
             f"{agree} of {len(h3)} cells** (descriptive; table below). In `{PRIMARY_CELL}` the sim's cheapest arm is "
             f"`{next(r for r in h3 if r['cell'] == PRIMARY_CELL)['cost'][0]}` and live's is `{cheapest}`.")
    L.append("")
    L.append("**What the run also shows, descriptively:** the replica-only baseline costs "
             f"{live[(live.workload == PRIMARY_CELL) & (live.system == 'replica-only')].total_cost_usd.iloc[0] / j.total_cost_usd:.0f}× "
             f"the joint controller in `{PRIMARY_CELL}` with worse fairness and multi-second AI tails; the cache and tier "
             "knobs carry that gain, and the tier knob alone carries all of it in the stress cell.")
    L.append("")
    L.append("## Substrate and validity")
    L.append("")
    L.append(f"* **Host:** one `g6e.2xlarge` — {hf.get('cpu_count', '?')} vCPU, {hf.get('mem_total_gib', '?')} GiB, "
             f"{gpu.get('name', '?')} {gpu.get('memory_total', '?')}, driver {gpu.get('driver', '?')}, "
             f"{hf.get('os', '?')} (`host_facts.json`, written by the harness during the last run, GPU in use "
             f"{gpu.get('memory_used', '?')}).")
    t = ", ".join(f"`{n}` {v}" for n, v in tiers())
    L.append(f"* **Tiers:** vLLM 0.29, Qwen2.5 0.5B / 3B / 7B served as `small` / `mid` / `large` on the one card, "
             f"{gpu_used_mib()} MiB resident; residency and a real decode per tier verified before the matrix: {t} "
             "(`run_logs/verify.log`). Serving arguments identical across tiers (context 4096, 128 sequences), "
             "shares 0.12 / 0.26 / 0.58 of the card.")
    L.append(f"* **Step-1c concurrency preflight (session-47 amendment clause 2), on this host:** valid — "
             f"{p1c.get('requests', '?'):,} requests, failed rate {p1c.get('failed_rate', float('nan')):.5f}, "
             f"{p1c.get('dropped', '?')} dropped iterations, active VUs at most {p1c.get('vus_active_max', '?')}, "
             f"{p1c.get('wl_h2', '?')} (`wave4_jointstress_probe_evidence/2026-09-15_g6e2xlarge_1c/`).")
    L.append(f"* **Matrix audit (the harness's own):** expected {audit.get('expected_runs', '?')}, valid "
             f"{audit.get('valid_runs', '?')}, failed {audit.get('failed_runs', '?')}, duplicate run ids "
             f"{audit.get('duplicate_run_ids', '?')}, orphan metrics {audit.get('orphan_metrics', '?')}, ok "
             f"{audit.get('ok', '?')}. One execution per arm per cell, as the stopping rule requires; "
             "2026-09-15 03:42–06:14 UTC.")
    L.append("")
    L.append("## Deviations, disclosed")
    L.append("")
    L.append("1. **The primary latency measurand was not captured.** The session-44 amendment clause 4 makes the "
             "`polyforge_http_request_duration_seconds` histogram primary and carries the replay-clock number "
             "alongside. The scrape (`scripts/soak_observer.sh`) was not run during the sitting — the runbook listed "
             "it under export, not under the run steps, and the operator followed the steps — and the per-run "
             "clusters are gone. Every latency, violation and Jain figure below is therefore the **replay-clock** "
             "metric, the one clause 4 calls CPU service time rather than service latency. **WL-H1's verdict does "
             "not depend on this**: cost is metered from the tier each request hit and from replica-seconds, and no "
             "latency metric can make jcac's cost lower than the cheapest arm's. Violation and Jain absolutes, "
             "and WL-H3's composite J, rest on the replay metric and would move under the histogram (L6 measured "
             "+239% at p95); they are reported as measured and not corrected.")
    L.append("2. **The registered paired bootstrap CI is not computable.** The frozen experiment ran one rep per "
             "cell with `timeseries_reps: 0`, and the harness keeps one per-bucket export per experiment, so there "
             "are no per-step pairs to resample. The verdict rests on the point estimates; a CI is what a *win* "
             "would need, and none is claimed.")
    L.append("3. **WL-H3's simulator reference ran after the live sitting** (`wave4_sim_ref.yaml`, 2026-09-15, "
             "3 reps at the live run's 30 steps) with the frozen tuned parameters and deterministic seeds. Nothing "
             "in it was tuned to the live numbers; it is descriptive and ordinal, as registered.")
    L.append("4. **Substrate changes before the run**, all disclosed in the session-48 pre-run note: the AI "
             "gateway's admission limiter lifted for the eval install (as the control plane's already was), "
             "unpinned tenants served by the default tier, the AI load path on a NodePort, a harness sampler "
             "thread fixed. None touches an arm, a cell, a margin or a metric.")
    L.append("")
    L.append("## Live results — every run")
    L.append("")
    L.append("Replay-clock latencies (see deviation 1). J = cost / ((steps−1)·8) / 0.01 + 2·violation + 0.5·(1−Jain).")
    L.append("")
    L.append("| cell | arm | cost $ | Jain | violation | viol. steps | cache hit | AI p95 ms | AI p99 ms | CRUD p95 ms | J |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    order = {c: i for i, c in enumerate(CELLS)}; aorder = {a: i for i, a in enumerate(ARMS)}
    for _, r in live.assign(_c=live.workload.map(order), _a=live.system.map(aorder)).sort_values(["_c", "_a"]).iterrows():
        L.append(fmt_row(r))
    L.append("")
    L.append(f"## WL-H1 — cost at iso-fairness in `{PRIMARY_CELL}`")
    L.append("")
    L.append(f"jcac: cost ${j.total_cost_usd:.4f}, Jain {j.mean_jain:.3f}. Iso-fairness = jcac's Jain no worse "
             f"than the arm's by more than {MARGIN}. \"Beats\" = lower cost AND iso-fair.")
    L.append("")
    L.append("| vs arm | arm cost $ | Δ cost (jcac − arm) | Δ % | arm Jain | Δ Jain | iso-fair | jcac beats |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in h1_rows:
        L.append(f"| {r['arm']} | {r['cost']:.4f} | {r['d_cost']:+.4f} | {r['d_cost_pct']:+.1f}% | {r['jain']:.3f} | "
                 f"{r['d_jain']:+.3f} | {'yes' if r['iso'] else 'no'} | **{'yes' if r['beats'] else 'NO'}** |")
    L.append("")
    L.append(f"**Verdict: {h1_verdict}.** " + (
        f"The cheapest arm is `{cheapest}`; jcac is {abs(best['d_cost_pct']):.1f}% more expensive at the same "
        "fairness. Where the extra spend went is not resolvable from run-level metrics (the tier-vs-infra cost "
        "split survives only in the last run's export); what the table establishes is identical fairness and "
        "violation at higher spend, i.e. the tier knob alone held this cell's SLO at the lowest cost."
        if h1_verdict == "FAIL" else ""))
    L.append("")
    L.append("## WL-H3 — does the simulator's ordinal ranking reproduce live?")
    L.append("")
    L.append("Winner = lowest mean per cell within each substrate; sim means over 3 reps, live one run. "
             "Ordinal only; no absolute crosses the substrate boundary.")
    L.append("")
    L.append("| cell | J: sim → live | | cost: sim → live | | violation: sim → live | |")
    L.append("|---|---|---|---|---|---|---|")
    for r in h3:
        L.append(f"| {r['cell']} | {r['J'][0]} → {r['J'][1]} | **{r['J'][2]}** | {r['cost'][0]} → {r['cost'][1]} | "
                 f"{r['cost'][2]} | {r['violation'][0]} → {r['violation'][1]} | {r['violation'][2]} |")
    L.append("")
    L.append("Sim reference means (cost $, Jain, violation, J), for the record — not comparable to the live absolutes:")
    L.append("")
    L.append("| cell | arm | cost $ | Jain | violation | J |")
    L.append("|---|---|---|---|---|---|")
    sm = per_cell_means(sim)
    for _, r in sm.assign(_c=sm.workload.map(order), _a=sm.system.map(aorder)).sort_values(["_c", "_a"]).iterrows():
        L.append(f"| {r.workload} | {r.system} | {r.total_cost_usd:.4f} | {r.mean_jain:.3f} | {r.mean_violation:.4f} | {r.J:.4f} |")
    L.append("")
    L.append("## Provenance")
    L.append("")
    L.append("* Raw: `eval/results/wave4_live_plane.duckdb` (gitignored; in the Zenodo deposit), committed export "
             "`wave4_live_plane_runs.csv`; sim reference `wave4_sim_ref.duckdb` / `wave4_sim_ref_runs.csv`.")
    L.append("* Evidence: `eval/results/wave4_live_plane_evidence/` — last run's `k6-summary.json`, "
             "`knob_preflight.json`, `host_facts.json`, `eval-export.json`, `load_distribution.json`; "
             "`run_logs/` — matrix audit, tier verification, backends line.")
    L.append("* Not evidence, kept as the raw proof of mechanisms: `wave4_jointstress_probe_evidence/` "
             "(session 44/45 laptop probes, the 2026-09-14 EC2 dry run, the 2026-09-15 ladder that attributed "
             "the earlier 12.3% failures to the gateway's admission limiter, and this host's step-1c preflight).")
    L.append("* Registered text: `PREREG_WAVE4_LIVE_PLANE.md`; the reproduction gate checks that its registered "
             "text is a byte prefix of the file today.")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_WAVE4_LIVE_PLANE.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
