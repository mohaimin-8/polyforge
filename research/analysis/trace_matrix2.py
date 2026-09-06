"""Powered replay on real BurstGPT demand (PREREG_TRACE2.md, frozen).

Second, independently seeded sample of the PREREG_TRACE protocol: 96
windows (48 per contiguous segment), 5 systems, 480 runs. Reuses the
committed machinery from trace_matrix.py — demand construction, scale
formula, pairing, tables — with the §2 deltas of PREREG_TRACE2. Workers
rebuild each window's demand deterministically from (window, seed), so
every system sees identical jittered demand without shipping gigabyte
pickles across processes.

    python trace_matrix2.py            # replay + analysis
    python trace_matrix2.py --analyze  # re-emit RESULTS_TRACE2.md from the CSV
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

import trace_matrix as tm
from trace_matrix import (  # noqa: F401  (shared frozen constants)
    HT_BASELINES, LABELS, REPO_ROOT, WINDOW_H, load_events, md_table,
    paired, run_one, scale_factor, segments_of,
)

import stats  # noqa: E402  (research/analysis/stats.py)

# Written through stats.record_path like every other generator. It used to
# resolve to this directory unconditionally, which meant the committed
# record was the ONLY place it could write: running the script overwrote
# the published record, and scripts/reproduce.py could not regenerate it
# into a scratch directory to compare. Both problems, one line.
OUT_MD = stats.record_path("RESULTS_TRACE2.md")
OUT_CSV = REPO_ROOT / "eval" / "results" / "trace_replay2_runs.csv"

# --- PREREG_TRACE2 §2 deltas (frozen) ------------------------------------
WINDOWS_PER_SEGMENT = 48
SEED_BASE = 2000
SYSTEMS_UNDER_TEST = ["jcac", "jcac_v2", "hpa", "keda", "firm"]

_DF = None
_K = None


def _init_worker() -> None:
    """Load the trace once per worker process."""
    global _DF, _K
    _DF = load_events()
    _K = scale_factor(_DF, segments_of(_DF))


def _run_job(args: tuple) -> dict:
    system, widx, start_s = args
    import simulate

    buckets = simulate.jitter_buckets(
        tm.window_buckets(_DF, start_s, _K), SEED_BASE + widx)
    row = run_one_with_buckets(system, widx, buckets)
    return row


def run_one_with_buckets(system: str, widx: int, buckets) -> dict:
    """run_one with prebuilt buckets and this prereg's seed base."""
    saved = tm.SEED_BASE
    tm.SEED_BASE = SEED_BASE
    try:
        return run_one((system, widx, buckets))
    finally:
        tm.SEED_BASE = saved


def window_starts(segs: list[tuple[int, int]]) -> list[int]:
    starts, w = [], WINDOW_H * 3600
    for a, b in segs:
        span = (b - a) - w
        starts.extend(
            int(a + i * span / (WINDOWS_PER_SEGMENT - 1))
            for i in range(WINDOWS_PER_SEGMENT)
        )
    return starts


def analyze(runs: pd.DataFrame, k: float, segs: list[tuple[int, int]]) -> None:
    lines: list[str] = []
    w = lines.append
    w("# Powered replay on real BurstGPT demand — results (pre-registered, second sample)")
    w("")
    w(f"Source: `burstgpt_real.csv.gz` (10,632,194 real requests), {len(segs)} "
      f"contiguous segments, {len(runs.window.unique())} windows x {WINDOW_H} h, "
      f"demand scale k = {k:.1f}; independent jitter seeds ({SEED_BASE}+i). "
      "Protocol frozen in `PREREG_TRACE2.md` before the run; the first sample "
      "(`RESULTS_TRACE.md`, n=16) stands as measured and is never pooled with "
      "this one. Rerun analysis: `python trace_matrix2.py --analyze`.")
    w("")
    w("## Per-system summary (mean over windows)")
    w("")
    summary = runs.groupby("system").mean(numeric_only=True).drop(columns=["window", "steps"])
    order = [s for s in SYSTEMS_UNDER_TEST if s in summary.index]
    pretty = summary.loc[order].reset_index()
    pretty["system"] = pretty.system.map(LABELS)
    w(md_table(pretty))
    w("")
    w("## HT2 (confirmatory, PREREG_TRACE2 §3) — composite J, paired by window")
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
        w(f"**HT2: PASS with disclosure** — the J wins vs "
          f"{', '.join(LABELS[b] for b in disclosures)} come with a large-effect "
          "violation regression (p<0.01, |d_z|≥0.5): PolyForge trades some "
          "attainment for its cost advantage on real demand, stated prominently "
          "per §3.")
    else:
        w(f"**HT2: {'PASS' if ht_ok else 'FAIL'}** — reported as measured; "
          "stopping rule §4: there is no third sample either way.")
    w("")
    w("## Declared secondary (estimates)")
    w("")
    half = WINDOWS_PER_SEGMENT
    rows = []
    for baseline in HT_BASELINES:
        m = runs[runs.system == "jcac"].merge(
            runs[runs.system == baseline], on="window", suffixes=("_a", "_b"))
        for seg, sub in (("segment 1", m[m.window < half]),
                         ("segment 2", m[m.window >= half])):
            rows.append({
                "baseline": LABELS[baseline], "segment": seg,
                "J diff": float((sub.J_a - sub.J_b).mean()),
                "cost diff (USD)": float(
                    (sub.total_cost_usd_a - sub.total_cost_usd_b).mean()),
            })
    w(md_table(pd.DataFrame(rows)))
    w("")
    r = paired(runs, "jcac_v2", "jcac", "J")
    w(f"ET1 replication (`jcac_v2` − `jcac` on J): diff {r['mean_diff']:+.4g}, "
      f"p={r['p']:.3g}, d_z={r['dz']:.3f}.")
    w("")
    w("## Notes")
    w("")
    w("- Same substrate rules as the first sample: model-scale dollars, ranking "
      "is the claim, tables never mixed with matrix results (ground rule 4).")
    w("- Latency metrics are p95, not p99 (documented deviation).")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--analyze", action="store_true")
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
    jobs = [(system, widx, start_s)
            for widx, start_s in enumerate(starts)
            for system in SYSTEMS_UNDER_TEST]
    print(f"{len(starts)} windows x {WINDOW_H}h -> {len(jobs)} runs")

    rows = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker) as pool:
        for i, row in enumerate(pool.map(_run_job, jobs), 1):
            rows.append(row)
            if i % 40 == 0 or i == len(jobs):
                print(f"  [{i}/{len(jobs)}]")
    runs = pd.DataFrame(rows).sort_values(["system", "window"])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    runs.to_csv(OUT_CSV, index=False)
    print(f"wrote {OUT_CSV} ({len(runs)} runs)")
    analyze(runs, k, segs)


if __name__ == "__main__":
    main()
