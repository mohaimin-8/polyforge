"""PREREG_SHED_ACCOUNTING.md -> RESULTS_SHED_ACCOUNTING.md.

Request-level shed accounting on the registered runs of JN and TA, after the
validity gate SA-V (every registered metric replays bit-identically).
Descriptive: no test, no verdict. FROZEN with the protocol.

    python analysis_shed_accounting.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stats import record_path  # noqa: E402

RESULTS = HERE.parents[1] / "eval" / "results"
RECORD = "RESULTS_SHED_ACCOUNTING.md"
ARMS = ["jcac_converged", "jcac_decoupled", "jcac_tier_floor", "hpa_fair_capped", "keda_fair_capped",
        "layered_small_tuned", "static_share"]
TREATMENT = "jcac_converged"
DESIGN = ["workload", "tenant_mix", "cluster_size"]
KEYS = ["run_id", "system"] + DESIGN + ["rep"]
REGISTERED = ["total_cost_usd", "mean_violation", "violation_step_share", "mean_jain", "cache_hit_rate",
              "crud_p95_ms", "ai_p95_ms", "mean_excess", "tier_none_step_share", "steps"]
ACCOUNTING = ["ai_shed_request_share", "shed_request_share", "cost_per_served_request"]
# campaign -> (rerun DB, rerun export, committed registered export, expected runs, reps)
CAMPAIGNS = {
    "JN": ("raw_sim_shed_jointness.duckdb", "metrics_shed_jointness.csv.gz",
           "metrics_matrix_jointness.csv.gz", 2100, 5),
    "TA": ("raw_sim_shed_trace.duckdb", "metrics_shed_trace.csv.gz", "metrics_trace_audited.csv.gz", 672, 1),
}


def load(campaign: str) -> pd.DataFrame | None:
    db, export, _, _, _ = CAMPAIGNS[campaign]
    if (RESULTS / db).exists():
        import duckdb

        con = duckdb.connect(str(RESULTS / db), read_only=True)
        try:
            return con.execute(
                "select " + ", ".join(f"r.{c}" for c in KEYS) + ", "
                + ", ".join(f"m.{c}" for c in REGISTERED + ACCOUNTING)
                + " from runs r join metrics m on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
        finally:
            con.close()
    if (RESULTS / export).exists():
        return pd.read_csv(RESULTS / export, float_precision="round_trip")[KEYS + REGISTERED + ACCOUNTING]
    return None


def gate(campaign: str, df: pd.DataFrame) -> tuple[bool, list[str]]:
    """SA-V: every registered metric equals the committed row of its run_id."""
    _, _, registered, expected, _ = CAMPAIGNS[campaign]
    ref = pd.read_csv(RESULTS / registered, float_precision="round_trip").set_index("run_id")
    new = df.set_index("run_id")
    missing = sorted(set(new.index) - set(ref.index))
    shared = new.index.intersection(ref.index)
    lines = [f"- runs rerun: {len(new):,} of {expected:,} expected; matched to a registered row: "
             f"{len(shared):,}; without one: {len(missing)}"]
    bad = 0
    for col in REGISTERED:
        a, b = new.loc[shared, col], ref.loc[shared, col]
        diff = int((a != b).sum())
        bad += diff
        if diff:
            lines.append(f"- `{col}`: {diff} mismatches (max |Δ| {float((a - b).abs().max()):.3g})")
    ok = bad == 0 and not missing and len(new) == expected
    lines.append(f"- registered metrics compared: {len(REGISTERED)} × {len(shared):,} values; mismatches: {bad}")
    return ok, lines


def per_unit(df: pd.DataFrame) -> pd.DataFrame:
    """Design point (JN; reps averaged) or window (TA), listwise complete over ARMS."""
    units = df.groupby(DESIGN + ["system"])[REGISTERED + ACCOUNTING].mean().reset_index()
    arms = units.groupby(DESIGN).system.nunique()
    keep = set(arms[arms == len(ARMS)].index)
    return units[[k in keep for k in zip(*(units[c] for c in DESIGN))]]


def arm_table(units: pd.DataFrame) -> list[str]:
    out = ["| arm | cost $ | AI shed, tenant-steps | AI shed, AI requests | shed, all requests | "
           "$ per 1,000 served |", "|---|---:|---:|---:|---:|---:|"]
    for arm in ARMS:
        s = units[units.system == arm]
        out.append(f"| `{arm}` | {s.total_cost_usd.mean():.3f} | {s.tier_none_step_share.mean():.1%} | "
                   f"{s.ai_shed_request_share.mean():.1%} | {s.shed_request_share.mean():.1%} | "
                   f"{1000.0 * s.cost_per_served_request.mean():.4f} |")
    return out


def treatment_spread(units: pd.DataFrame, by_class: bool) -> list[str]:
    t = units[units.system == TREATMENT]
    out = [f"`{TREATMENT}`, AI-request shed share over units: median {t.ai_shed_request_share.median():.1%}, "
           f"max {t.ai_shed_request_share.max():.1%}; units with any shed request: "
           f"{int((t.ai_shed_request_share > 0).sum())} of {len(t)}.", ""]
    if by_class:
        out += ["| workload class | AI shed, tenant-steps | AI shed, AI requests | median | max |",
                "|---|---:|---:|---:|---:|"]
        for wl, s in t.groupby("workload"):
            out.append(f"| `{wl}` | {s.tier_none_step_share.mean():.1%} | {s.ai_shed_request_share.mean():.1%} | "
                       f"{s.ai_shed_request_share.median():.1%} | {s.ai_shed_request_share.max():.1%} |")
        out.append("")
    return out


def build(frames: dict[str, pd.DataFrame]) -> str:
    L = ["# How many requests does shedding drop?", "",
         "Generated by `analysis_shed_accounting.py`, frozen with `PREREG_SHED_ACCOUNTING.md`. Descriptive: "
         "no test, no verdict. A shed request is an AI request answered by no model: a cache miss on a "
         "tenant-step whose tier is \"none\" (a cache hit is answered by a stored answer; CRUD is always "
         "served). Means over the design point (JN, reps averaged) or the window (TA), listwise complete.", ""]
    titles = {"JN": "Main matrix (JN runs: 60 design points × 5 reps, 120 steps)",
              "TA": "Real BurstGPT demand (TA runs: 96 six-hour windows, 2,159 steps)"}
    for campaign, df in frames.items():
        ok, lines = gate(campaign, df)
        L += [f"## {titles[campaign]}", "", f"**SA-V (bit-identical replay): {'HOLDS' if ok else 'FAILS'}**", ""]
        L += lines + [""]
        if not ok:
            L += ["The counters changed what ran, or runs are missing: no counter is quoted from this campaign.", ""]
            continue
        units = per_unit(df)
        n = units[DESIGN].drop_duplicates().shape[0]
        L += [f"{n} complete units.", ""] + arm_table(units) + [""]
        L += treatment_spread(units, by_class=campaign == "JN")
    return "\n".join(L) + "\n"


def main() -> int:
    frames = {c: f for c in CAMPAIGNS if (f := load(c)) is not None}
    out = record_path(RECORD)
    if len(frames) < len(CAMPAIGNS):
        out.write_text(f"# Shed accounting\n\nNot all reruns have run: {sorted(frames)}\n", encoding="utf-8")
    else:
        out.write_text(build(frames), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
