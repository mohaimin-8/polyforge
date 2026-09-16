"""B1'', the damped joint controller on the live plane -> RESULTS_WAVE4_DWELL.md.

PROTOCOL: PREREG_WAVE4_DWELL.md, committed with this file BEFORE the run.
Every reading below is the registered one.

Inputs
- eval/results/wave4_dwell_plane.duckdb (gitignored, Zenodo-archived), else
  the committed export wave4_dwell_plane_runs.csv: 3 arms x 4 cells x 2 reps.
- eval/results/wave4_dwell_plane_evidence/runs/<arm>__<cell>__uniform__small__rep<N>/
  eval-export-fine.json: per-10-s-bucket cost_usd (the bootstrap's unit) and
  cost_infra_usd (the replica trajectory: one .status.replicas sample per
  bucket, priced) -- both scored from the load window only (--since).

Readings
- WL-H6 (primary, damping): in every AI-heavy cell the dwell arm changes
  its replica count at most HALF as often as jcac-calibrated (mean changes
  per window over reps) and its largest single change is no larger.
- WL-H7 (primary, no regression): in joint_stress the dwell arm's cost at
  iso-fairness is not worse than jcac-calibrated's -- the paired-bootstrap
  95% CI on (dwell - calibrated) per bucket has its upper bound at or below
  +NONINFERIORITY of calibrated's mean bucket cost -- and it still beats
  tier-only (CI below 0, Jain within MARGIN).
- WL-H2 (gating): PASS count from the matrix log.

    python analysis_wave4_dwell.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import statistics
import sys
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stats import record_path  # noqa: E402

_spec = importlib.util.spec_from_file_location("aw", HERE / "analysis_wave4_calibrated.py")
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)

ROOT = HERE.parents[1]
RESULTS = ROOT / "eval" / "results"
DB, CSV = RESULTS / "wave4_dwell_plane.duckdb", RESULTS / "wave4_dwell_plane_runs.csv"
EVIDENCE = RESULTS / "wave4_dwell_plane_evidence"

TREATMENT = "jcac-calibrated-dwell"
REFERENCE = "jcac-calibrated"
ABLATION = "tier-only"
ARMS = [TREATMENT, REFERENCE, ABLATION]
CELLS = ["ai_cacheable", "tier_mixed", "crud_bursty", "joint_stress"]
AI_CELLS = ["ai_cacheable", "tier_mixed", "joint_stress"]
PRIMARY_CELL = "joint_stress"
MARGIN = 0.01
NONINFERIORITY = 0.05
CHURN_RATIO = 0.5
REPLICA_COST_USD_HR = 0.048
REPLICA_SAMPLE_S = 10.0
PRICE_PER_REPLICA_SAMPLE = REPLICA_COST_USD_HR / 3600.0 * REPLICA_SAMPLE_S


def load() -> pd.DataFrame:
    if DB.exists():
        con = duckdb.connect(str(DB), read_only=True)
        df = con.execute(
            "select r.system, r.workload, r.rep, m.total_cost_usd, m.mean_violation, m.mean_jain, "
            "m.cache_hit_rate, m.crud_p95_ms, m.ai_p95_ms, m.crud_p99_ms, m.ai_p99_ms "
            "from runs r join metrics m on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
        con.close()
    elif CSV.exists():
        raw = pd.read_csv(CSV, float_precision="round_trip")
        raw = raw[raw.status == "valid"]
        df = raw[aw.COLS].copy()
        for c in aw.COLS[3:]:
            df[c] = pd.to_numeric(df[c])
    else:
        raise FileNotFoundError(f"{DB.name} missing and no committed export {CSV.name}")
    return df.reset_index(drop=True)


def run_dir(arm: str, cell: str, rep: int) -> Path:
    return EVIDENCE / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"


def _buckets(arm: str, cell: str, rep: int) -> list[dict] | None:
    p = run_dir(arm, cell, rep) / "eval-export-fine.json"
    if not p.exists():
        return None
    b = json.loads(p.read_text(encoding="utf-8")).get("buckets") or []
    return b or None


def window_costs(arm: str, cell: str, rep: int) -> list[float] | None:
    """With --since the export starts at the window; the span rule of B1'
    (Amendment 4) is kept so a stray tail bucket is trimmed the same way."""
    b = _buckets(arm, cell, rep)
    if not b or "cost_usd" not in b[0]:
        return None
    if "n_events" not in b[0]:
        return [float(x["cost_usd"]) for x in b]
    floor = aw.WINDOW_EVENT_FRACTION * sorted(int(x["n_events"]) for x in b)[len(b) // 2]
    idx = [i for i, x in enumerate(b) if int(x["n_events"]) >= floor]
    return [float(x["cost_usd"]) for x in b[idx[0]:idx[-1] + 1]] if idx else None


def trajectory(arm: str, cell: str, rep: int) -> list[int] | None:
    b = _buckets(arm, cell, rep)
    if not b or "cost_infra_usd" not in b[0]:
        return None
    if "n_events" in b[0]:
        floor = aw.WINDOW_EVENT_FRACTION * sorted(int(x["n_events"]) for x in b)[len(b) // 2]
        idx = [i for i, x in enumerate(b) if int(x["n_events"]) >= floor]
        b = b[idx[0]:idx[-1] + 1] if idx else b
    t = [round(x["cost_infra_usd"] / PRICE_PER_REPLICA_SAMPLE) for x in b if x["cost_infra_usd"] > 0]
    return t or None


def churn(arm: str, cell: str, reps: list[int]) -> dict:
    changes, largest = [], []
    for rep in reps:
        t = trajectory(arm, cell, rep)
        if not t:
            continue
        deltas = [b - a for a, b in zip(t, t[1:]) if b != a]
        changes.append(len(deltas))
        largest.append(max((abs(d) for d in deltas), default=0))
    return {"changes": statistics.fmean(changes) if changes else None,
            "largest": max(largest) if largest else None, "reps": len(changes)}


def paired(cell: str, a: str, b: str, reps: list[int]) -> list[float]:
    out = []
    for rep in reps:
        ca, cb = window_costs(a, cell, rep), window_costs(b, cell, rep)
        if ca and cb:
            out.extend(x - y for x, y in zip(ca, cb))
    return out


def matrix_audit() -> tuple[dict, int]:
    p = EVIDENCE / "run_logs" / "matrix.log"
    if not p.exists():
        return {}, 0
    text = p.read_text(encoding="utf-8", errors="replace")
    reports = re.findall(r"\{[^{}]*\"experiment\"[^{}]*\}", text, re.S)
    audit = json.loads(reports[-1]) if reports else {}
    audit["runner_passes"] = len(reports)
    return audit, len(re.findall(r"verdict: WL-H2 PASS", text))


def fmt_ci(ci) -> str:
    return "n/a" if ci is None else f"[{ci[0]:+.4f}, {ci[1]:+.4f}]"


def build() -> str:
    df = load()
    reps = sorted(int(r) for r in df.rep.unique())
    m = aw.means(df)
    audit, h2 = matrix_audit()

    # WL-H6
    h6 = []
    for cell in AI_CELLS:
        t, r = churn(TREATMENT, cell, reps), churn(REFERENCE, cell, reps)
        ok = (t["changes"] is not None and r["changes"] is not None and r["changes"] > 0
              and t["changes"] <= CHURN_RATIO * r["changes"] and t["largest"] <= r["largest"])
        h6.append({"cell": cell, "t": t, "r": r, "pass": ok})
    h6_verdict = "PASS" if h6 and all(x["pass"] for x in h6) else "FAIL"

    # WL-H7
    def row(cell: str, arm: str) -> dict:
        tm = m[(m.workload == cell) & (m.system == TREATMENT)]
        om = m[(m.workload == cell) & (m.system == arm)]
        if tm.empty or om.empty:
            return {"arm": arm, "void": True}
        t, o = tm.iloc[0], om.iloc[0]
        deltas = paired(cell, TREATMENT, arm, reps)
        ci = aw.bootstrap_ci(deltas)
        arm_buckets = [b for rep in reps for b in (window_costs(arm, cell, rep) or [])]
        mean_bucket = statistics.fmean(arm_buckets) if arm_buckets else None
        return {"arm": arm, "void": False, "cost": float(o.total_cost_usd), "t_cost": float(t.total_cost_usd),
                "d_cost": float(t.total_cost_usd - o.total_cost_usd), "d_jain": float(t.mean_jain - o.mean_jain),
                "iso": float(t.mean_jain - o.mean_jain) >= -MARGIN, "n_pairs": len(deltas), "ci": ci,
                "mean_bucket": mean_bucket}
    vs_ref = row(PRIMARY_CELL, REFERENCE)
    vs_abl = row(PRIMARY_CELL, ABLATION)
    noninf = (not vs_ref["void"] and vs_ref["ci"] is not None and vs_ref["mean_bucket"]
              and vs_ref["ci"][1] <= NONINFERIORITY * vs_ref["mean_bucket"] and vs_ref["iso"])
    beats_abl = (not vs_abl["void"] and vs_abl["ci"] is not None and vs_abl["ci"][1] < 0 and vs_abl["iso"])
    h7_verdict = "PASS" if noninf and beats_abl else "FAIL"

    L = [f"# B1″ — the damped joint controller on the live plane: WL-H6 {h6_verdict}, WL-H7 {h7_verdict}", "",
         "Generated by `analysis_wave4_dwell.py` from `eval/results/wave4_dwell_plane_runs.csv` (the committed "
         "export of `wave4_dwell_plane.duckdb`) and the per-run evidence under "
         "`eval/results/wave4_dwell_plane_evidence/runs/`. Pre-registration: `PREREG_WAVE4_DWELL.md`, committed "
         "with this scorer before the run. Metrics are scored from the load window only (`--since`), so run-level "
         "costs are NOT comparable to B1/B1′'s, which carried the WL-H2 preflight's spend.", "",
         "## Headline", "",
         f"**WL-H6 — {h6_verdict}.** " + "; ".join(
             f"`{x['cell']}`: dwell {x['t']['changes'] if x['t']['changes'] is not None else 'n/a'} changes "
             f"(largest {x['t']['largest']}) vs calibrated {x['r']['changes'] if x['r']['changes'] is not None else 'n/a'} "
             f"(largest {x['r']['largest']}) → {'pass' if x['pass'] else 'FAIL'}" for x in h6) + ".", "",
         f"**WL-H7 — {h7_verdict}.** In `{PRIMARY_CELL}`: vs `{REFERENCE}` Δ cost "
         + (f"{vs_ref['d_cost']:+.4f} ({100 * vs_ref['d_cost'] / vs_ref['cost']:+.1f}%), Δ Jain {vs_ref['d_jain']:+.3f}, "
            f"CI/bucket {fmt_ci(vs_ref['ci'])} against a non-inferiority bound of "
            f"+{NONINFERIORITY * (vs_ref['mean_bucket'] or 0):.4f} → {'non-inferior' if noninf else 'NOT shown non-inferior'}"
            if not vs_ref["void"] else "void")
         + f"; vs `{ABLATION}` "
         + (f"Δ cost {vs_abl['d_cost']:+.4f} ({100 * vs_abl['d_cost'] / vs_abl['cost']:+.1f}%), CI/bucket {fmt_ci(vs_abl['ci'])} "
            f"→ {'beats' if beats_abl else 'does NOT beat'}" if not vs_abl["void"] else "void") + ".", "",
         f"**WL-H2 — {h2} PASS verdicts**; harness audit after the last of {audit.get('runner_passes', '?')} runner "
         f"pass(es): expected {audit.get('expected_runs', '?')}, valid {audit.get('valid_runs', '?')}, "
         f"failed {audit.get('failed_runs', '?')}.", "",
         "## Replica churn — every cell, both controllers", "",
         "| cell | arm | mean changes per window | largest single change | reps |", "|---|---|---|---|---|"]
    for cell in CELLS:
        for arm in (TREATMENT, REFERENCE):
            c = churn(arm, cell, reps)
            L.append(f"| {cell} | {arm} | {c['changes'] if c['changes'] is not None else 'n/a'} | "
                     f"{c['largest'] if c['largest'] is not None else 'n/a'} | {c['reps']} |")
    L += ["", "## Every run — means over reps", "",
          "| cell | arm | cost $ (window) | Jain | violation | cache hit | AI p95 ms | CRUD p95 ms |",
          "|---|---|---|---|---|---|---|---|"]
    order = {c: i for i, c in enumerate(CELLS)}; aorder = {a: i for i, a in enumerate(ARMS)}
    for _, r in m.assign(_c=m.workload.map(order), _a=m.system.map(aorder)).sort_values(["_c", "_a"]).iterrows():
        L.append(f"| {r.workload} | {r.system} | {r.total_cost_usd:.4f} | {r.mean_jain:.3f} | {r.mean_violation:.4f} | "
                 f"{r.cache_hit_rate:.2f} | {r.ai_p95_ms:.0f} | {r.crud_p95_ms:.1f} |")
    L += ["", "## Notes", "",
          "* Churn counts replica-count changes between consecutive 10-s samples inside the load window, recovered "
          "from `cost_infra_usd` per bucket exactly as `CHURN_WAVE4_CALIBRATED.md` does for B1′.",
          "* The paired bootstrap is the B1′ one (buckets paired by position within the window, reps pooled, "
          f"{aw.N_BOOT} resamples, seed {aw.SEED}); the non-inferiority bound is {int(100 * NONINFERIORITY)}% of the "
          "reference arm's mean bucket cost.", ""]
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_WAVE4_DWELL.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
