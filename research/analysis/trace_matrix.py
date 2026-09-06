"""Headline replay on real BurstGPT demand (PREREG_TRACE.md, frozen §2).

Replays 16 deterministic 6-hour windows of the real BurstGPT v2.0 trace
(scaled to the matrix's operating point, shape untouched) through the six
frozen systems, then runs the pre-registered paired analysis and writes
RESULTS_TRACE.md + eval/results/trace_replay_runs.csv. Every moving part
is imported from committed code: simulate.run / jitter_buckets /
default_configs, the harness SYSTEMS registry, tuned.yaml, the W28 LRU
miss-cost factor, and the W34 composite objective.

    python trace_matrix.py            # replay (resumes nothing; one shot) + analysis
    python trace_matrix.py --analyze  # re-emit RESULTS_TRACE.md from the CSV
"""

from __future__ import annotations

import argparse
import gzip
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
from scipy import stats as sps

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "research" / "jcac_sim"))
sys.path.insert(0, str(REPO_ROOT / "eval"))

import stats  # noqa: E402  (research/analysis/stats.py)

import simulate  # noqa: E402
from controller import ClusterLimits, Weights  # noqa: E402
from model import CONTROL_INTERVAL_S, WORK_UNITS, Demand  # noqa: E402
from harness.systems import SYSTEMS, lru_miss_cost_factor, tuned_params  # noqa: E402

TRACE = REPO_ROOT / "research" / "traces" / "out" / "burstgpt_real.csv.gz"
# Written through stats.record_path like every other generator. It used to
# resolve to this directory unconditionally, which meant the committed
# record was the ONLY place it could write: running the script overwrote
# the published record, and scripts/reproduce.py could not regenerate it
# into a scratch directory to compare. Both problems, one line.
OUT_MD = stats.record_path("RESULTS_TRACE.md")
OUT_CSV = REPO_ROOT / "eval" / "results" / "trace_replay_runs.csv"

# --- frozen protocol constants (PREREG_TRACE.md §2) ----------------------
RATE_BUCKET_S = 600          # finest resolution the real arrival density supports
TARGET_WU_PER_TENANT = 100.0  # one replica's capacity: the matrix's operating point
WINDOW_H = 6
WINDOWS_PER_SEGMENT = 8
GAP_BUCKETS_H = 24            # segment break: >= 1 silent day at hourly resolution
SEED_BASE = 1000
TENANTS = 8
SYSTEMS_UNDER_TEST = ["jcac", "jcac_v2", "jcac_seasonal", "hpa", "keda", "firm"]
HT_BASELINES = ["hpa", "keda", "firm"]
MEDIUM = ClusterLimits(cache_mb=4096, replicas=48)

LABELS = {
    "jcac": "PolyForge (v1 trend)", "jcac_v2": "PolyForge v2 (Holt)",
    "jcac_seasonal": "PolyForge (seasonal)", "hpa": "HPA", "keda": "KEDA",
    "firm": "FIRM",
}


def load_events() -> pd.DataFrame:
    with gzip.open(TRACE, "rt") as f:
        df = pd.read_csv(f, usecols=["timestamp_ms", "tenant_id"])
    df["s"] = df.timestamp_ms // 1000
    return df


def segments_of(df: pd.DataFrame) -> list[tuple[int, int]]:
    """Contiguous coverage [start_s, end_s) split on >= GAP_BUCKETS_H empty
    hours — the same missing-data rule as FORECAST_TRACE_REAL.md."""
    hours = (df.s // 3600).unique()
    hours.sort()
    segs, start, prev = [], int(hours[0]), int(hours[0])
    for h in hours[1:]:
        if h - prev >= GAP_BUCKETS_H:
            segs.append((start * 3600, (prev + 1) * 3600))
            start = int(h)
        prev = int(h)
    segs.append((start * 3600, (prev + 1) * 3600))
    return segs


def scale_factor(df: pd.DataFrame, segs: list[tuple[int, int]]) -> float:
    covered_s = sum(b - a for a, b in segs)
    mean_rps_per_tenant = len(df) / covered_s / TENANTS
    return TARGET_WU_PER_TENANT / (WORK_UNITS["chat"] * mean_rps_per_tenant)


def window_starts(segs: list[tuple[int, int]]) -> list[int]:
    starts, w = [], WINDOW_H * 3600
    for a, b in segs:
        span = (b - a) - w
        starts.extend(int(a + i * span / (WINDOWS_PER_SEGMENT - 1)) for i in range(WINDOWS_PER_SEGMENT))
    return starts


def window_buckets(df: pd.DataFrame, start_s: int, k: float) -> list[dict[str, Demand]]:
    """Per-CONTROL_INTERVAL demand for one window: real per-tenant 600s
    rates, scaled by k, held piecewise-constant. Jitter is applied by the
    caller once per window so every system sees identical demand."""
    end_s = start_s + WINDOW_H * 3600
    rows = df[(df.s >= start_s) & (df.s < end_s)]
    counts = rows.groupby([(rows.s - start_s) // RATE_BUCKET_S, "tenant_id"]).size()
    tenant_ids = [f"t{i:02d}" for i in range(TENANTS)]
    buckets = []
    for coarse in range(WINDOW_H * 3600 // RATE_BUCKET_S):
        per_tenant = {
            tid: Demand(rps={"chat": k * counts.get((coarse, tid), 0) / RATE_BUCKET_S})
            for tid in tenant_ids
        }
        buckets.extend([per_tenant] * (RATE_BUCKET_S // CONTROL_INTERVAL_S))
    return buckets


def run_one(args: tuple) -> dict:
    system, widx, buckets = args
    spec = SYSTEMS[system]
    tenant_ids = [f"t{i:02d}" for i in range(TENANTS)]
    configs = simulate.default_configs(tenant_ids)
    params = dict(tuned_params().get(spec.controller, {}))
    params.update(spec.params)
    if spec.seeded:
        params["seed"] = SEED_BASE + widx
    # Mirror `eval/harness/sim_backend.py` on the two spec attributes this
    # substrate previously dropped on the floor, so an arm means the same
    # thing here as it does on the matrix substrate (PREREG_TRACE_PARITY):
    # `static_cache_mb` pins the arm's cache (the reactive baselines carry
    # `state.cache_mb` forward unchanged, so the initial value holds for the
    # whole run), and `evict_overhead_us` is a simulator argument, not a
    # controller parameter. Both are None on every published arm, so every
    # published record replays bit-identically (R4).
    evict_overhead_us = params.pop("evict_overhead_us", None)
    # `gamma` was the only weight this substrate could express; PREREG_
    # VIOLATION_PARITY sweeps `beta` (the SLO weight), so both are forwarded.
    # Both default to None on every published arm, leaving `weights=None` and
    # the engine defaults exactly as before (R4).
    weight_kw = {k: v for k, v in (("gamma", spec.gamma),
                                   ("beta", getattr(spec, "beta", None)))
                 if v is not None}
    weights = Weights(**weight_kw) if weight_kw else None
    result = simulate.run(
        spec.controller, tenant_ids, buckets,
        configs=configs, weights=weights, limits=MEDIUM,
        collect_rows=False, controller_params=params or None,
        miss_cost_factor=lru_miss_cost_factor() if spec.lru_eviction else 1.0,
        initial_cache_mb=spec.static_cache_mb,
        evict_overhead_ms=(evict_overhead_us / 1000.0) if evict_overhead_us else None,
    )
    scored = result.steps * TENANTS
    j = (result.total_cost_usd / scored / 0.01
         + 2.0 * result.mean_violation + 0.5 * (1.0 - result.mean_jain))
    return {
        "system": system, "window": widx,
        "total_cost_usd": result.total_cost_usd,
        "mean_violation": result.mean_violation,
        "violation_step_share": result.violation_step_share,
        "mean_jain": result.mean_jain,
        "cache_hit_rate": result.cache_hit_rate,
        # PREREG_TRACE_PARITY §Design: the engine computes these and this
        # substrate discarded them. `mean_violation` saturates at 1.0, so a 2x
        # SLO miss and a shed AI service score identically; `mean_excess` is the
        # unbounded overshoot and `tier_none_step_share` the shed rate.
        # `total_tier_cost_usd` is unscaled, so the eviction sensitivity band is
        # recomputed exactly. Appending columns cannot change any published
        # record: `--analyze` reads the committed CSV, which does not have them.
        "mean_excess": result.mean_excess,
        "tier_none_step_share": result.tier_none_step_share,
        "total_tier_cost_usd": result.total_tier_cost_usd,
        "J": j, "steps": result.steps,
    }


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    m = df[df.system == a].merge(df[df.system == b], on="window", suffixes=("_a", "_b"))
    diff = m[f"{metric}_a"] - m[f"{metric}_b"]
    sd = diff.std(ddof=1)
    if len(diff) < 2 or sd == 0.0:
        return {"mean_diff": float(diff.mean()) if len(diff) else 0.0,
                "p": 1.0, "dz": 0.0, "n": len(diff)}
    _, p = sps.ttest_1samp(diff, 0.0)
    return {"mean_diff": float(diff.mean()), "p": float(p),
            "dz": float(diff.mean() / sd), "n": len(diff)}


def md_table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    fmt = lambda v: f"{v:.4g}" if isinstance(v, float) else str(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |" for _, r in frame.iterrows()]
    return "\n".join(lines)


def analyze(runs: pd.DataFrame, k: float, segs: list[tuple[int, int]]) -> None:
    lines: list[str] = []
    w = lines.append
    w("# Headline replay on real BurstGPT demand — results (pre-registered)")
    w("")
    w(f"Source: `{TRACE.name}` (10,632,194 real requests), {len(segs)} contiguous "
      f"segments, {len(runs.window.unique())} windows x {WINDOW_H} h at "
      f"{CONTROL_INTERVAL_S}s control intervals; demand scale k = {k:.1f} "
      "(PREREG_TRACE.md §2, frozen before the run; real shape, matrix "
      "magnitude). Engine, systems, and tuning identical to the committed "
      "headline. Own substrate — never mixed into matrix tables (ground rule 4). "
      "Rerun analysis: `python trace_matrix.py --analyze`.")
    w("")
    w("## Per-system summary (mean over windows)")
    w("")
    summary = runs.groupby("system").mean(numeric_only=True).drop(columns=["window", "steps"])
    order = [s for s in SYSTEMS_UNDER_TEST if s in summary.index]
    pretty = summary.loc[order].reset_index()
    pretty["system"] = pretty.system.map(LABELS)
    w(md_table(pretty))
    w("")
    w("## HT (confirmatory, PREREG_TRACE §3) — composite J, paired by window")
    w("")
    rows, ht_ok, disclosures = [], True, []
    for baseline in HT_BASELINES:
        j = paired(runs, "jcac", baseline, "J")
        cost = paired(runs, "jcac", baseline, "total_cost_usd")
        slo = paired(runs, "jcac", baseline, "mean_violation")
        ok = j["mean_diff"] < 0 and j["p"] < 0.01
        ht_ok &= ok
        if ok and slo["mean_diff"] > 0 and slo["p"] < 0.01 and abs(slo["dz"]) >= 0.5:
            disclosures.append(baseline)
        rows.append({
            "baseline": LABELS[baseline], "n": j["n"],
            "J diff (jcac−base)": j["mean_diff"], "p": j["p"], "d_z": j["dz"],
            "cost diff (USD)": cost["mean_diff"], "cost p": cost["p"],
            "violation diff": slo["mean_diff"], "violation p": slo["p"],
            "verdict": "PASS" if ok else "FAIL",
        })
    w(md_table(pd.DataFrame(rows)))
    w("")
    if ht_ok and disclosures:
        w(f"**HT: PASS with disclosure** — J wins vs "
          f"{', '.join(LABELS[b] for b in disclosures)} come with a large-effect "
          "violation regression (p<0.01, |d_z|≥0.5), stated prominently per §3.")
    else:
        w(f"**HT: {'PASS' if ht_ok else 'FAIL'}** — reported as measured; "
          "stopping rule §4 forbids re-running either way.")
    w("")
    w("## Declared exploratory (PREREG_TRACE §3)")
    w("")
    rows = []
    for name, a, b in (("ET1: Holt vs trend", "jcac_v2", "jcac"),
                       ("ET2: seasonal vs trend", "jcac_seasonal", "jcac")):
        for metric in ("J", "total_cost_usd", "mean_violation"):
            r = paired(runs, a, b, metric)
            rows.append({"comparison": name, "metric": metric,
                         "diff (a−b)": r["mean_diff"], "p": r["p"], "d_z": r["dz"]})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("ET3 — per-segment J diff (jcac − baseline), mean over that segment's windows:")
    w("")
    half = WINDOWS_PER_SEGMENT
    rows = []
    for baseline in HT_BASELINES:
        m = runs[runs.system == "jcac"].merge(
            runs[runs.system == baseline], on="window", suffixes=("_a", "_b"))
        for seg, sub in (("segment 1", m[m.window < half]), ("segment 2", m[m.window >= half])):
            rows.append({"baseline": LABELS[baseline], "segment": seg,
                         "J diff": float((sub.J_a - sub.J_b).mean())})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("## Notes")
    w("")
    w("- Windows are the replication unit; each window's Poisson arrival "
      "jitter is drawn once and shared by all six systems (exact pairing).")
    w("- The demand scale k maps the trace to the calibrated operating point; "
      "the *shape* (real diurnal, real burst structure) is untouched. "
      "Absolute dollar values are therefore model-scale, not Azure-scale — "
      "the claim is the ranking, per ground rule 4.")
    w("- Latency metrics are p95, not p99 (documented deviation).")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--analyze", action="store_true", help="re-emit MD from committed CSV")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    df = load_events()
    segs = segments_of(df)
    k = scale_factor(df, segs)
    print(f"segments={[(a//3600, b//3600) for a, b in segs]} (hours), k={k:.1f}")

    if args.analyze and OUT_CSV.exists():
        analyze(pd.read_csv(OUT_CSV), k, segs)
        return

    starts = window_starts(segs)
    print(f"{len(starts)} windows x {WINDOW_H}h; jittering once per window")
    jobs = []
    for widx, start_s in enumerate(starts):
        buckets = simulate.jitter_buckets(window_buckets(df, start_s, k), SEED_BASE + widx)
        jobs.extend((system, widx, buckets) for system in SYSTEMS_UNDER_TEST)

    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(run_one, jobs), 1):
            rows.append(row)
            if i % 12 == 0 or i == len(jobs):
                print(f"  [{i}/{len(jobs)}]")
    runs = pd.DataFrame(rows).sort_values(["system", "window"])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    runs.to_csv(OUT_CSV, index=False)
    print(f"wrote {OUT_CSV} ({len(runs)} runs)")
    analyze(runs, k, segs)


if __name__ == "__main__":
    main()
