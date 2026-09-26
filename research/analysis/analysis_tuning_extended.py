"""Extended baseline tuning -> TUNING_EXTENDED.md (exploratory).

Reads the committed sweeps: `eval/baselines/grids/` (the published W34 grids),
`eval/baselines/grids_extended/` (`tune_extended.py`: each grid pushed past
its edge on the same slice, seeds and objective; hpa, keda and vtc_replica at
all three cluster sizes; jcac_converged and static scored on the same slice)
and `eval/baselines/grids_arms/` (`tune_arms.py`: the FAIR_J comparator arms
tuned as the harness runs them). Changes no published tuned value.

    python analysis_tuning_extended.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stats import record_path  # noqa: E402

BASELINES = HERE.parents[1] / "eval" / "baselines"
PUBLISHED = BASELINES / "grids"
EXTENDED = BASELINES / "grids_extended"
RECORD = "TUNING_EXTENDED.md"
MEDIUM = ["hpa", "keda", "concurrency", "firm", "gptcache", "vtc_replica"]
PER_SIZE = ["hpa", "keda", "vtc_replica"]
SIZES = ["small", "medium", "large"]
TERMS = ["cost_norm", "violation", "unfair"]
ARM_GRIDS = BASELINES / "grids_arms"
ARMS = {"hpa_fair": "target_rho", "keda_fair": "rps_per_replica", "jcac_nojoint_v2_tuned": "target_rho"}
NOT_PARAMS = {"mean_objective", "wall_s", *TERMS}
# Values a parameter cannot go below: an optimum there is the knob's limit,
# not a truncated grid (one stable interval = no stabilisation window).
FLOORS = {"stable_intervals": 1}


def params_of(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in NOT_PARAMS]


def optimum(df: pd.DataFrame) -> pd.Series:
    return df.loc[df.mean_objective.idxmin()]


def edges(df: pd.DataFrame, row: pd.Series) -> list[str]:
    """Parameters whose optimum is the lowest or highest value swept, unless
    that value is the parameter's floor."""
    return [p for p in params_of(df)
            if df[p].nunique() > 1 and row[p] in (df[p].min(), df[p].max())
            and row[p] != FLOORS.get(p)]


def fmt(df: pd.DataFrame, row: pd.Series) -> str:
    return ", ".join(f"{p}={row[p]:g}" for p in params_of(df))


def extended(name: str, size: str) -> pd.DataFrame:
    return pd.read_csv(EXTENDED / (f"{name}.csv" if size == "medium" else f"{name}_{size}.csv"))


def reference(size: str) -> dict[str, pd.Series]:
    df = pd.read_csv(EXTENDED / f"reference_{size}.csv")
    return {r.arm: r for _, r in df.iterrows()}


def medium_table() -> tuple[list[str], list[tuple[str, float, float]]]:
    L = ["| baseline | published optimum | J | extended optimum | J | cost | violation | 1−Jain | "
         "still on an edge? |", "|---|---|---:|---|---:|---:|---:|---:|---|"]
    gains = []
    for name in MEDIUM:
        pub, ext = pd.read_csv(PUBLISHED / f"{name}.csv"), extended(name, "medium")
        p, e = optimum(pub), optimum(ext)
        gains.append((name, float(p.mean_objective), float(e.mean_objective)))
        L.append(f"| `{name}` | {fmt(pub, p)} | {p.mean_objective:.5f} | {fmt(ext, e)} | "
                 f"{e.mean_objective:.5f} | {e.cost_norm:.4f} | {e.violation:.4f} | {e.unfair:.4f} | "
                 f"{', '.join(edges(ext, e)) or 'no'} |")
    return L, gains


def size_table() -> tuple[list[str], dict[str, tuple[str, float, float]]]:
    L = ["| cluster | `jcac_converged` | `static` | " + " | ".join(f"best `{n}`" for n in PER_SIZE)
         + " | best baseline − JCAC |", "|---|---:|---:|" + "---|" * len(PER_SIZE) + "---:|"]
    verdict = {}
    for size in SIZES:
        ref = reference(size)
        jcac = float(ref["jcac_converged"].mean_objective)
        cells, best = [], ("", float("inf"))
        for name in PER_SIZE:
            df = extended(name, size)
            r = optimum(df)
            edge = edges(df, r)
            cells.append(f"{r.mean_objective:.5f} ({fmt(df, r)}{'; edge' if edge else ''})")
            if r.mean_objective < best[1]:
                best = (name, float(r.mean_objective))
        verdict[size] = (best[0], best[1], jcac)
        L.append(f"| {size} | {jcac:.5f} | {ref['static'].mean_objective:.5f} | " + " | ".join(cells)
                 + f" | {best[1] - jcac:+.5f} ({(best[1] - jcac) / jcac:+.1%}) |")
    return L, verdict


def vtc_table() -> tuple[list[str], dict[str, bool], bool]:
    L = ["| cluster | target_rho values where VTC-replica ≠ HPA | of |", "|---|---:|---:|"]
    engaged = {}
    for size in SIZES:
        h = extended("hpa", size).set_index("target_rho").mean_objective
        v = extended("vtc_replica", size).set_index("target_rho").mean_objective
        common = h.index.intersection(v.index)
        differ = int(((h[common] - v[common]).abs() > 1e-9).sum())
        engaged[size] = differ > 0
        L.append(f"| {size} | {differ} | {len(common)} |")
    h = pd.read_csv(PUBLISHED / "hpa.csv").set_index("target_rho").mean_objective
    v = pd.read_csv(PUBLISHED / "vtc_replica.csv").set_index("target_rho").mean_objective
    identical = bool(h.index.equals(v.index) and ((h - v).abs() <= 1e-9).all())
    return L, engaged, identical


def arm_table() -> tuple[list[str], dict[str, dict[str, float]]]:
    """The FAIR_J comparator arms tuned as the harness runs them (tune_arms.py)."""
    L = ["| arm | cluster | published setting | J | re-tuned setting | J | `jcac_converged` | "
         "re-tuned arm − JCAC |", "|---|---|---|---:|---|---:|---:|---:|"]
    import yaml

    published = yaml.safe_load((BASELINES / "tuned.yaml").read_text(encoding="utf-8"))
    rel = {}
    for arm, knob in ARMS.items():
        rel[arm] = {}
        base = published["keda" if knob == "rps_per_replica" else "hpa"][knob]
        for size in SIZES:
            df = pd.read_csv(ARM_GRIDS / f"{arm}_{size}.csv")
            at = df.loc[df[knob] == base].iloc[0]
            best = optimum(df)
            jcac = float(reference(size)["jcac_converged"].mean_objective)
            rel[arm][size] = (best.mean_objective - jcac) / jcac
            L.append(f"| `{arm}` | {size} | {knob}={base:g} | {at.mean_objective:.5f} | "
                     f"{knob}={best[knob]:g} | {best.mean_objective:.5f} | {jcac:.5f} | "
                     f"{best.mean_objective - jcac:+.5f} ({rel[arm][size]:+.1%}) |")
    return L, rel


def reading(gains, verdict, engaged, identical, rel) -> list[str]:
    hpa_pub, hpa_ext = next((a, b) for n, a, b in gains if n == "hpa")
    static_worst = min(float(reference(s)["static"].mean_objective) for s in SIZES)
    hpa = extended("hpa", "medium")
    turns = "before it turns" if not edges(hpa, optimum(hpa)) else "and is still falling at the last value swept"
    L = [f"- **The published bounds were not a formality.** `tune.py` bounded every grid on the ground "
         f"that J falls monotonically toward static over-provisioning, which `static` covers. On the same "
         f"slice HPA's J falls from {hpa_pub:.5f} (ρ = 0.3, the published edge) to {hpa_ext:.5f} "
         f"({(hpa_ext - hpa_pub) / hpa_pub:+.1%}) {turns}, while `static` never scores below "
         f"{static_worst:.3f} at any size: the excluded region holds the baselines' best settings, and "
         f"`static` does not stand in for it."]
    big = max(gains, key=lambda g: (g[1] - g[2]) / g[1])
    L.append(f"- **The largest move is `{big[0]}`**: {big[1]:.5f} → {big[2]:.5f} "
             f"({(big[2] - big[1]) / big[1]:+.1%}). Every published comparison against it ran against a "
             f"setting this far from its own optimum.")
    lost = [s for s, (_, b, j) in verdict.items() if b < j]
    kept = [s for s in SIZES if s not in lost]
    if lost:
        L.append("- **Against re-tuned baselines JCAC no longer wins every size on the tuning slice.** "
                 + "; ".join(f"{s}: best `{verdict[s][0]}` {verdict[s][1]:.5f} vs JCAC {verdict[s][2]:.5f}"
                             for s in lost)
                 + (f". JCAC keeps its lead on {', '.join(kept)}." if kept else "."))
    else:
        L.append("- **JCAC keeps its lead at every size** against the best re-tuned replica baseline on the "
                 "tuning slice.")
    where = [s for s in SIZES if engaged[s]]
    L.append("- **VTC-replica**: " + (
        "within the published medium grid (ρ 0.3–0.8) its scores equal HPA's at every value, so its "
        "fair-division rule never engaged there and its published tuned value is HPA's. " if identical else "")
        + (f"Once the pool binds it does engage ({', '.join(where)})." if where
           else "It never differs from HPA at any size swept."))
    beats = [(a, s) for a, by in rel.items() for s, r in by.items() if r < 0]
    L.append("- **The comparator arms, re-tuned as the harness runs them**, " + (
        "beat JCAC on the tuning slice in: " + ", ".join(f"`{a}` at {s} ({rel[a][s]:+.1%})" for a, s in beats)
        + ". " if beats else "never beat JCAC on the tuning slice. ")
        + "Their re-tuned values are frozen in `eval/baselines/tuned_arms.yaml` and read only by the "
        "`*_retuned` arms.")
    L.append("- **What this does and does not change.** The tuning slice is 12 runs per setting on seeds "
             "disjoint from every campaign; it locates optima, it does not test hypotheses. It tunes the bare "
             "controllers as `tune.py` does; the confirmatory comparators wrap them (`hpa_fair` and `keda_fair` "
             "add a 512 MB cache without the LRU cost penalty), which is why the arm table tunes them as "
             "the harness runs them. "
             "Every published "
             "comparison ran against the bounded optima, and `RESULTS_FAIR_J.md`'s tie with `hpa_fair` is a "
             "tie with HPA at ρ = 0.3. Whether JCAC's FAIR_J results survive re-tuned comparators is a new "
             "confirmatory question on fresh seeds; this record only establishes that it has to be asked.")
    return L


def build() -> str:
    med, gains = medium_table()
    sizes, verdict = size_table()
    vtc, engaged, identical = vtc_table()
    arms, rel = arm_table()
    L = ["# Extended baseline tuning: the published optima were grid edges", "",
         "Generated by `analysis_tuning_extended.py` from `eval/baselines/grids/` (the published W34 "
         "sweeps) and `eval/baselines/grids_extended/` (`tune_extended.py`). Same tuning slice, seeds and "
         "objective as `tune.py` (3 workloads × 2 mixes × 2 reps, 120 steps; J = cost + 2·violation + "
         "0.5·(1−Jain); lower is better). **Exploratory. No published tuned value changes: every published "
         "arm still reads `tuned.yaml` alone.**", "",
         "## Medium cluster (the published tuning slice)", "", *med, "",
         "## Every size: the best replica baseline against JCAC and static", "",
         "`jcac_converged` and `static` are scored on the same slice. 'edge' marks an optimum at the last "
         "value swept.", "", *sizes, "",
         "## Does VTC's fair-division rule engage?", "", *vtc, "",
         "## The FAIR_J comparator arms, tuned as the harness runs them", "",
         "`tune_arms.py` replays `sim_backend.execute`'s arm settings (starting cache, miss-cost factor, "
         "parameters) on the same slice at every size; every optimum below is interior to its grid.", "",
         *arms, "",
         "## Reading", "", *reading(gains, verdict, engaged, identical, rel), ""]
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path(RECORD)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
