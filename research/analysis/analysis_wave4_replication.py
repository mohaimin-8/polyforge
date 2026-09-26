"""PREREG_WAVE4_REPLICATION.md -> RESULTS_WAVE4_REPLICATION.md.

The live three-knob comparison replicated on five independent shared seeds,
with a held-out cell, the corrected replica-only arm and a tuned live HPA
(audit 2026-09-26, Phase 4). FROZEN with the pre-registration; not edited
after the run.

The unit is the SEED (rep): B1' pooled 10-second buckets across reps, so its
independent sample was one host x one seed. Here each (cell, rep) is one
shared-seed pairing of all six arms.

    python analysis_wave4_replication.py
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stats import record_path  # noqa: E402

ROOT = HERE.parents[1]
RESULTS = ROOT / "eval" / "results"
DB = RESULTS / "wave4_replication.duckdb"
CSV = RESULTS / "metrics_wave4_replication.csv.gz"
RECORD = "RESULTS_WAVE4_REPLICATION.md"

TREATMENT = "jcac-calibrated"
COMPARATORS = ["jcac", "replica-only-tuned", "replica-only", "cache-only", "tier-only"]
ARMS = [TREATMENT, *COMPARATORS]
PRIMARY_CELL = "joint_stress"      # WL-R1 (the B1' WL-H1' cell)
HELD_OUT_CELL = "agentic"          # WL-R2 (never used to design the controller)
IN_SAMPLE_CELL = "ai_cacheable"    # descriptive replication of a B1' cell
CELLS = [PRIMARY_CELL, HELD_OUT_CELL, IN_SAMPLE_CELL]
REPS = 5
ALPHA = 0.05
JAIN_MARGIN = 0.01                 # iso-fairness, as WL-H1'
VIOLATION_MARGIN = 0.01            # iso-attainment, new here
OPERATOR_ARMS = {"jcac-calibrated", "jcac", "cache-only", "tier-only"}
STEP_SECONDS = 10
STEPS = 30
REPLICA_COST_USD_HR = 0.048        # eval/harness/cluster_backend.py
COLUMNS = ["system", "workload", "rep", "total_cost_usd", "mean_jain", "mean_violation",
           "cache_hit_rate", "ai_p95_ms", "crud_p95_ms"]


def _millicores(text: str) -> float:
    return float(text[:-1]) if text.endswith("m") else 1000.0 * float(text)


def control_overhead_usd() -> float:
    """Per run: the operator + planner pods, which only the operator-driven
    arms run, priced as control-plane replica-equivalents by CPU request over
    the scored window (one replica of each, the charts' default)."""
    op = yaml.safe_load((ROOT / "deploy" / "helm" / "polyforge-operator" / "values.yaml")
                        .read_text(encoding="utf-8"))
    app = yaml.safe_load((ROOT / "deploy" / "helm" / "polyforge" / "values.yaml")
                         .read_text(encoding="utf-8"))
    extra = (_millicores(op["operator"]["resources"]["requests"]["cpu"])
             + _millicores(op["planner"]["resources"]["requests"]["cpu"]))
    per_replica = _millicores(app["resources"]["requests"]["cpu"])
    return extra / per_replica * REPLICA_COST_USD_HR * STEPS * STEP_SECONDS / 3600.0


def load() -> pd.DataFrame:
    if DB.exists():
        import duckdb

        con = duckdb.connect(str(DB), read_only=True)
        try:
            return con.execute(
                "select r.system, r.workload, r.rep, m.total_cost_usd, m.mean_jain, "
                "m.mean_violation, m.cache_hit_rate, m.ai_p95_ms, m.crud_p95_ms "
                "from runs r join metrics m on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
        finally:
            con.close()
    return pd.read_csv(CSV, float_precision="round_trip")[COLUMNS]


def complete_reps(df: pd.DataFrame, cell: str) -> list[int]:
    """Reps where every arm has a valid run in `cell` (listwise)."""
    sub = df[df.workload == cell]
    have = sub.groupby("rep").system.apply(set)
    return sorted(int(r) for r, arms in have.items() if set(ARMS) <= arms)


def sign_flip_p(d: np.ndarray) -> float:
    """Exact one-sided p that the mean paired difference is below zero:
    the share of the 2^n sign assignments whose mean is at or below the
    observed one."""
    obs = d.mean()
    flips = [np.mean(d * np.array(s)) for s in itertools.product((1, -1), repeat=len(d))]
    return float(np.mean([f <= obs + 1e-15 for f in flips]))


def compare(df: pd.DataFrame, cell: str, comp: str, overhead: float = 0.0) -> dict:
    reps = complete_reps(df, cell)
    sub = df[df.workload == cell].set_index(["system", "rep"])

    def col(arm: str, c: str) -> np.ndarray:
        v = np.array([float(sub.loc[(arm, r), c]) for r in reps])
        if c == "total_cost_usd" and overhead and arm in OPERATOR_ARMS:
            v = v + overhead
        return v

    d_cost = col(TREATMENT, "total_cost_usd") - col(comp, "total_cost_usd")
    d_jain = col(TREATMENT, "mean_jain") - col(comp, "mean_jain")
    d_viol = col(TREATMENT, "mean_violation") - col(comp, "mean_violation")
    evaluable = len(reps) == REPS
    p = sign_flip_p(d_cost) if len(reps) else 1.0
    iso_fair = bool(len(reps) and d_jain.mean() >= -JAIN_MARGIN)
    iso_slo = bool(len(reps) and d_viol.mean() <= VIOLATION_MARGIN)
    return {"vs": comp, "n": len(reps), "evaluable": evaluable,
            "d_cost": float(d_cost.mean()) if len(reps) else float("nan"),
            "cheaper_in": int((d_cost < 0).sum()), "p": p,
            "d_jain": float(d_jain.mean()) if len(reps) else float("nan"),
            "d_viol": float(d_viol.mean()) if len(reps) else float("nan"),
            "iso_fair": iso_fair, "iso_slo": iso_slo,
            "beats": bool(evaluable and p <= ALPHA and iso_fair and iso_slo)}


def verdict(df: pd.DataFrame, cell: str, overhead: float = 0.0) -> tuple[str, list[dict]]:
    """Intersection-union: the treatment must beat EVERY comparator, each at
    level ALPHA unadjusted (the conjunction then holds at ALPHA)."""
    rows = [compare(df, cell, c, overhead) for c in COMPARATORS]
    if not all(r["evaluable"] for r in rows):
        return "NOT EVALUABLE", rows
    return ("PASS" if all(r["beats"] for r in rows) else "FAIL"), rows


def build(df: pd.DataFrame) -> str:
    overhead = control_overhead_usd()
    L = ["# The live three-knob comparison, replicated on five independent seeds", "",
         "Generated by `analysis_wave4_replication.py`, frozen with `PREREG_WAVE4_REPLICATION.md`. "
         f"Treatment `{TREATMENT}`. Unit: the seed -- each (cell, rep) is one shared-seed pairing of "
         f"all six arms; a rep missing for any arm is dropped for that cell. A comparator is beaten "
         f"when the exact one-sided sign-flip p of the per-seed cost differences is ≤ {ALPHA} (with "
         f"{REPS} seeds: cheaper on every one), mean Jain is no worse by more than {JAIN_MARGIN} and "
         f"mean violation no worse by more than {VIOLATION_MARGIN}. The hypothesis needs every "
         "comparator beaten (intersection-union, each at α unadjusted). Negative = treatment cheaper.", "",
         "## Per-arm means", "",
         "| cell | arm | seeds | cost $ | Jain | mean_violation | cache hit | AI p95 ms | CRUD p95 ms |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for cell in CELLS:
        for arm in ARMS:
            s = df[(df.workload == cell) & (df.system == arm)]
            if s.empty:
                L.append(f"| {cell} | `{arm}` | 0 | — | — | — | — | — | — |")
                continue
            L.append(f"| {cell} | `{arm}` | {len(s)} | {s.total_cost_usd.mean():.4f} | "
                     f"{s.mean_jain.mean():.4f} | {s.mean_violation.mean():.4f} | "
                     f"{s.cache_hit_rate.mean():.3f} | {s.ai_p95_ms.mean():.0f} | {s.crud_p95_ms.mean():.0f} |")

    def table(title: str, cell: str, oh: float = 0.0) -> list[str]:
        v, rows = verdict(df, cell, oh)
        out = ["", title, "", f"**Verdict: {v}**", "",
               "| vs | seeds | Δcost $ | cheaper in | sign-flip p | ΔJain | Δviolation | beaten |",
               "|---|---:|---:|---:|---:|---:|---:|---|"]
        for r in rows:
            out.append(f"| `{r['vs']}` | {r['n']} | {r['d_cost']:+.4f} | {r['cheaper_in']}/{r['n']} | "
                       f"{r['p']:.3f} | {r['d_jain']:+.4f} | {r['d_viol']:+.4f} | "
                       f"{'yes' if r['beats'] else 'no'} |")
        return out

    L += table(f"## WL-R1 (primary): `{PRIMARY_CELL}`", PRIMARY_CELL)
    L += table(f"## WL-R2 (secondary, out of sample): held-out `{HELD_OUT_CELL}`", HELD_OUT_CELL)
    L += table(f"## Guard: WL-R1 with control overhead charged (${overhead:.4f} per run to each "
               "operator-driven arm: operator + planner pods as control-plane replica-equivalents)",
               PRIMARY_CELL, overhead)
    L += table(f"## Descriptive: in-sample replication, `{IN_SAMPLE_CELL}`", IN_SAMPLE_CELL)
    L += ["", "## Descriptive: the two replica-only arms", "",
          "| cell | `replica-only` (chart HPA) | `replica-only-tuned` | difference |",
          "|---|---:|---:|---:|"]
    for cell in CELLS:
        a = df[(df.workload == cell) & (df.system == "replica-only")].total_cost_usd
        b = df[(df.workload == cell) & (df.system == "replica-only-tuned")].total_cost_usd
        if len(a) and len(b):
            L.append(f"| {cell} | {a.mean():.4f} | {b.mean():.4f} | {b.mean() - a.mean():+.4f} |")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path(RECORD)
    out.write_text(build(load()), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
