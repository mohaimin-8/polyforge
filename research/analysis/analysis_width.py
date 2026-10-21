"""PREREG_WIDTH.md -> RESULTS_WIDTH.md: does coordination between one tenant's
knobs pay once a single interval can move far, and does that depend on how
strongly the layers share capacity?

FROZEN with the pre-registration; not edited after the run. Every statistic
is the frozen PREREG_FAIR_J scorer's (design point as the unit, its listwise
rule, its bootstrap and its one-sided Wilcoxon), applied to the registered
differences below.

Per plant and design point (reps averaged):
    G_narrow = J(jcac_decoupled)      - J(jcac_joint_tracked)
    G_wide   = J(jcac_decoupled_wide) - J(jcac_joint_wide_tracked)
    DiD      = G_wide - G_narrow
G > 0 means the joint search is better; DiD > 0 means coordination gains more
on the wide lattice.

    python analysis_width.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from analysis_fair_j import (  # noqa: E402
    ALPHA, BOOT_N, BOOT_SEED, DESIGN, boot_means, complete_design_points, superiority,
)
from stats import composite_objective, holm_bonferroni, record_path  # noqa: E402

RESULTS = HERE.parents[1] / "eval" / "results"
RECORD = "RESULTS_WIDTH.md"
PLANTS = ("published", "coupled", "live")
NARROW_JOINT, NARROW_DEC = "jcac_joint_tracked", "jcac_decoupled"
WIDE_JOINT, WIDE_DEC = "jcac_joint_wide_tracked", "jcac_decoupled_wide"
ARMS = [NARROW_JOINT, NARROW_DEC, WIDE_JOINT, WIDE_DEC]
EQ_MARGIN = 0.025
SIZES = ["small", "medium", "large"]
COLUMNS = ["system", "workload", "tenant_mix", "cluster_size", "rep", "total_cost_usd",
           "mean_violation", "mean_jain", "mean_excess", "tier_none_step_share",
           "clamp_violation_step_share", "coordination_disagreement_share"]


def _db(plant: str) -> Path:
    return RESULTS / f"raw_sim_matrix_width_{plant}.duckdb"


def _csv(plant: str) -> Path:
    return RESULTS / f"metrics_matrix_width_{plant}.csv.gz"


def load(plant: str) -> pd.DataFrame:
    if _db(plant).exists():
        import duckdb

        con = duckdb.connect(str(_db(plant)), read_only=True)
        try:
            return con.execute(
                "select r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep, "
                "m.total_cost_usd, m.mean_violation, m.mean_jain, m.mean_excess, m.tier_none_step_share, "
                "m.clamp_violation_step_share, m.coordination_disagreement_share "
                "from runs r join metrics m on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
        finally:
            con.close()
    return pd.read_csv(_csv(plant), float_precision="round_trip")[COLUMNS]


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = complete_design_points(df, ARMS)
    return df.assign(J=composite_objective(df))


def _means(df: pd.DataFrame, arm: str) -> pd.Series:
    return df[df.system == arm].groupby(DESIGN).J.mean()


def gains(df: pd.DataFrame) -> pd.DataFrame:
    """Per design point: G_narrow, G_wide and DiD, in canonical order."""
    out = pd.DataFrame({
        "g_narrow": _means(df, NARROW_DEC) - _means(df, NARROW_JOINT),
        "g_wide": _means(df, WIDE_DEC) - _means(df, WIDE_JOINT),
        "rate_limit": _means(df, NARROW_JOINT) - _means(df, WIDE_JOINT),
    }).dropna().sort_index()
    return out.assign(did=out.g_wide - out.g_narrow)


def ci(d: np.ndarray) -> tuple[float, float]:
    means = boot_means(d)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def equivalent(d: np.ndarray) -> bool:
    lo, hi = ci(d)
    return -EQ_MARGIN < lo and hi < EQ_MARGIN


def score(frames: dict[str, pd.DataFrame]) -> dict:
    """The registered tests, each through the frozen superiority rule, whose
    'below 0' must be the registered direction: WD-H1/H2 test -DiD (so DiD >
    0), WD-H5 tests J(narrow joint) - J(wide joint) as it stands (narrow
    better)."""
    g = {p: gains(frames[p]) for p in PLANTS}
    mech = {"WD-H1": superiority(-g["published"].did.to_numpy()),
            "WD-H2": superiority(-g["coupled"].did.to_numpy())}
    holm = holm_bonferroni({h: r["p"] for h, r in mech.items()}, ALPHA)
    for h, r in mech.items():
        r["threshold"], r["pass"] = holm[h]["threshold"], bool(holm[h]["reject"] and r["ci"][1] < 0)
    rate = {f"WD-H5[{p}]": superiority(g[p].rate_limit.to_numpy()) for p in PLANTS}
    holm5 = holm_bonferroni({h: r["p"] for h, r in rate.items()}, ALPHA)
    for h, r in rate.items():
        r["threshold"], r["pass"] = holm5[h]["threshold"], bool(holm5[h]["reject"] and r["ci"][1] < 0)
    return {"gains": g, "mech": mech, "rate": rate,
            "WD-H3": equivalent(g["live"].did.to_numpy()),
            "WD-H4": {p: equivalent(g[p].g_narrow.to_numpy()) for p in PLANTS}}


def _fmt(lo_hi: tuple[float, float]) -> str:
    return f"[{lo_hi[0]:+.4f}, {lo_hi[1]:+.4f}]"


def build(frames: dict[str, pd.DataFrame]) -> str:
    frames = {p: prepare(f) for p, f in frames.items()}
    res = score(frames)
    g = res["gains"]
    L = ["# Does coordination pay once one interval can move far?", "",
         "Generated by `analysis_width.py`, frozen with `PREREG_WIDTH.md`. Three plants (published; "
         "coupled: AI work on the replicas 3x, mid/large tiers 2x/4x; live: fitted to B1', AI never on "
         "the replicas) x two lattices (published: +/-2 replicas, one cache level; wide: +/-8, any level) "
         "x joint vs decoupled search. Unit: the design point (reps averaged, the PREREG_FAIR_J listwise "
         f"rule); bootstrap {BOOT_N:,} resamples, seed {BOOT_SEED}. G = J(decoupled) - J(joint), so G > 0 "
         "means the joint search is better; DiD = G_wide - G_narrow.", ""]
    L += ["## Per plant: coordination gain on each lattice", "",
          "| plant | design points | G narrow [95% CI] | G wide [95% CI] | DiD [95% CI] | "
          "disagreement narrow | disagreement wide |", "|---|---:|---|---|---|---:|---:|"]
    for p in PLANTS:
        f, gp = frames[p], g[p]
        dn = f[f.system == NARROW_JOINT].coordination_disagreement_share.mean()
        dw = f[f.system == WIDE_JOINT].coordination_disagreement_share.mean()
        L.append(f"| {p} | {len(gp)} | {gp.g_narrow.mean():+.4f} {_fmt(ci(gp.g_narrow.to_numpy()))} | "
                 f"{gp.g_wide.mean():+.4f} {_fmt(ci(gp.g_wide.to_numpy()))} | "
                 f"{gp.did.mean():+.4f} {_fmt(ci(gp.did.to_numpy()))} | {dn:.1%} | {dw:.1%} |")
    L += ["", "## Confirmatory: WD-H1, WD-H2 (DiD > 0; one Holm family of 2)", "",
          "| id | plant | design points | mean DiD | 95% CI of DiD | Wilcoxon p | Holm threshold | verdict |",
          "|---|---|---:|---:|---|---:|---:|---|"]
    for h, p in (("WD-H1", "published"), ("WD-H2", "coupled")):
        r = res["mech"][h]
        L.append(f"| **{h}** | {p} | {r['n']} | {-r['mean']:+.4f} | {_fmt((-r['ci'][1], -r['ci'][0]))} | "
                 f"{r['p']:.3g} | {r['threshold']:.4f} | {'PASS' if r['pass'] else 'FAIL'} |")
    L += ["", f"## Registered equivalence readings (95% CI strictly inside ±{EQ_MARGIN})", "",
          "| id | reading | verdict |", "|---|---|---|",
          f"| WD-H3 | live plant: DiD equivalent to 0 | {'EQUIVALENT' if res['WD-H3'] else 'not shown'} |"]
    for p in PLANTS:
        L.append(f"| WD-H4[{p}] | {p} plant, published lattice: G equivalent to 0 | "
                 f"{'EQUIVALENT' if res['WD-H4'][p] else 'not shown'} |")
    L += ["", "## Confirmatory: WD-H5, rate-limited joint control beats wide joint control "
          "(J narrow < J wide; one Holm family of 3)", "",
          "| id | design points | J narrow - J wide | 95% CI | Wilcoxon p | Holm threshold | verdict |",
          "|---|---:|---:|---|---:|---:|---|"]
    for h, r in res["rate"].items():
        L.append(f"| **{h}** | {r['n']} | {r['mean']:+.4f} | {_fmt(r['ci'])} | {r['p']:.3g} | "
                 f"{r['threshold']:.4f} | {'PASS' if r['pass'] else 'FAIL'} |")
    L += ["", "## Per-arm means (descriptive)", "",
          "| plant | arm | J | cost $ | violation | excess | AI shed | clamp breaks |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for p in PLANTS:
        f = frames[p]
        for arm in ARMS:
            s = f[f.system == arm]
            L.append(f"| {p} | `{arm}` | {s.J.mean():.4f} | {s.total_cost_usd.mean():.4f} | "
                     f"{s.mean_violation.mean():.4f} | {s.mean_excess.mean():.4f} | "
                     f"{s.tier_none_step_share.mean():.1%} | {s.clamp_violation_step_share.mean():.1%} |")
    L += ["", "## DiD per workload and cluster size (descriptive)", "",
          "| plant | workload | small | medium | large |", "|---|---|---:|---:|---:|"]
    for p in PLANTS:
        gp = g[p].reset_index()
        for wl in sorted(gp.workload.unique()):
            row = [f"{gp[(gp.workload == wl) & (gp.cluster_size == z)].did.mean():+.4f}" for z in SIZES]
            L.append(f"| {p} | `{wl}` | " + " | ".join(row) + " |")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path(RECORD)
    out.write_text(build({p: load(p) for p in PLANTS}), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
