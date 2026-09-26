"""Replay on real Azure LLM 2024 demand (PREREG_TRACE_AZURE.md, frozen).

Second real demand trace for the replay substrate: 72 non-overlapping 3 h
windows tiling the 216 h trace exactly, 5 tuned systems, 360 runs. All
engine machinery is imported from the committed `trace_matrix.py`
(systems registry, tuned params, pairing, tables, composite J); this file
adds only the Azure loading rules frozen in the prereg — round-robin
pseudo-tenantization within each real stream and the 3 h window tiling.

The parent process reduces the 44.1M-row trace to per-window 600 s rate
matrices once; workers rebuild each window's buckets deterministically
from (rates, window index), so every system sees identical jittered
demand without the trace ever being loaded in a worker.

    python trace_matrix_azure.py            # replay + analysis
    python trace_matrix_azure.py --analyze  # re-emit RESULTS_TRACE_AZURE.md
    python trace_matrix_azure.py --smoke    # sanctioned discarded pipeline check
"""

from __future__ import annotations

import argparse
import gzip
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import trace_matrix as tm
from trace_matrix import (  # noqa: F401  (shared frozen machinery)
    HT_BASELINES, LABELS, REPO_ROOT, md_table, paired,
)
from model import CONTROL_INTERVAL_S, WORK_UNITS, Demand  # noqa: E402

import stats  # noqa: E402  (research/analysis/stats.py)

TRACE = REPO_ROOT / "research" / "traces" / "out" / "azure_llm_2024.csv.gz"
# Written through stats.record_path like every other generator. It used to
# resolve to this directory unconditionally, which meant the committed
# record was the ONLY place it could write: running the script overwrote
# the published record, and scripts/reproduce.py could not regenerate it
# into a scratch directory to compare. Both problems, one line.
OUT_MD = stats.record_path("RESULTS_TRACE_AZURE.md")
OUT_CSV = REPO_ROOT / "eval" / "results" / "trace_replay_azure_runs.csv"

# --- frozen protocol constants (PREREG_TRACE_AZURE.md §2) -----------------
WINDOW_H = 3
N_WINDOWS = 72
RATE_BUCKET_S = 600
TARGET_WU_PER_TENANT = 100.0
SEED_BASE = 3000
TENANTS = 8
PSEUDO_PER_STREAM = 4
STREAM_BASE = {"conv": 0, "code": 4}   # conv -> t00..t03, code -> t04..t07
SYSTEMS_UNDER_TEST = ["jcac", "jcac_v2", "hpa", "keda", "firm"]

COARSE_PER_WINDOW = WINDOW_H * 3600 // RATE_BUCKET_S
STEPS_PER_COARSE = RATE_BUCKET_S // CONTROL_INTERVAL_S


def load_rates() -> tuple[np.ndarray, float]:
    """(windows x coarse x tenants) request rates in rps, and the scale k.

    Round-robin pseudo-tenantization within each stream by row order (the
    ETL output is timestamp-sorted): pseudo-tenant = stream base +
    (row rank within stream) mod 4 — an exact quarter-thinning that
    carries the stream's real shape (prereg §2, disclosed)."""
    with gzip.open(TRACE, "rt") as f:
        df = pd.read_csv(f, usecols=["timestamp_ms", "tenant_id"],
                         dtype={"timestamp_ms": "int64", "tenant_id": "category"})
    s = (df.timestamp_ms // 1000).to_numpy()
    start = int(s.min())
    stream = df.tenant_id.to_numpy()
    tenant = np.empty(len(df), dtype=np.int8)
    for name, base in STREAM_BASE.items():
        mask = stream == name
        tenant[mask] = base + (np.arange(int(mask.sum())) % PSEUDO_PER_STREAM)

    coarse = (s - start) // RATE_BUCKET_S
    n_coarse = N_WINDOWS * COARSE_PER_WINDOW
    counts = np.zeros((n_coarse, TENANTS), dtype=np.int64)
    valid = coarse < n_coarse
    np.add.at(counts, (coarse[valid], tenant[valid]), 1)

    covered_s = n_coarse * RATE_BUCKET_S
    mean_rps_per_tenant = counts.sum() / covered_s / TENANTS
    k = TARGET_WU_PER_TENANT / (WORK_UNITS["chat"] * mean_rps_per_tenant)
    rates = (k * counts / RATE_BUCKET_S).reshape(N_WINDOWS, COARSE_PER_WINDOW, TENANTS)
    return rates, float(k)


def window_buckets(window_rates: np.ndarray) -> list[dict[str, Demand]]:
    """Piecewise-constant per-CONTROL_INTERVAL demand for one window."""
    tenant_ids = [f"t{i:02d}" for i in range(TENANTS)]
    buckets = []
    for coarse in range(COARSE_PER_WINDOW):
        per_tenant = {
            tid: Demand(rps={"chat": float(window_rates[coarse, i])})
            for i, tid in enumerate(tenant_ids)
        }
        buckets.extend([per_tenant] * STEPS_PER_COARSE)
    return buckets


_RATES = None


def _init_worker(rates: np.ndarray) -> None:
    global _RATES
    _RATES = rates


def _run_job(args: tuple) -> dict:
    system, widx = args
    import simulate

    buckets = simulate.jitter_buckets(window_buckets(_RATES[widx]), SEED_BASE + widx)
    saved = tm.SEED_BASE
    tm.SEED_BASE = SEED_BASE
    try:
        return tm.run_one((system, widx, buckets))
    finally:
        tm.SEED_BASE = saved


def conv_share(rates: np.ndarray) -> np.ndarray:
    """Per-window share of total demand carried by the conv pseudo-tenants."""
    conv = rates[:, :, STREAM_BASE["conv"]:STREAM_BASE["conv"] + PSEUDO_PER_STREAM].sum(axis=(1, 2))
    total = rates.sum(axis=(1, 2))
    return np.divide(conv, total, out=np.zeros_like(conv), where=total > 0)


def analyze(runs: pd.DataFrame, share: np.ndarray, k: float) -> None:
    """`share` is the per-window conv share (conv_share(rates)); it is all
    the record needs from the raw trace besides k (see trace_meta)."""
    lines: list[str] = []
    w = lines.append
    w("# Replay on real Azure LLM 2024 demand — results (pre-registered, second real trace)")
    w("")
    w(f"Source: `{TRACE.name}` (44,107,694 real requests, two production Azure "
      f"LLM services, 216 h contiguous), {N_WINDOWS} windows x {WINDOW_H} h "
      f"tiling the trace exactly; demand scale k = {k:.3f}; jitter seeds "
      f"{SEED_BASE}+i. Protocol frozen in `PREREG_TRACE_AZURE.md` (pushed "
      "before any run). Round-robin pseudo-tenantization within each real "
      "stream, disclosed in §2: within-stream pseudo-tenants are nearly "
      "perfectly demand-correlated (the harder packing regime). Never pooled "
      "with the BurstGPT samples (ground rules 3-4). Rerun analysis: "
      "`python trace_matrix_azure.py --analyze`.")
    w("")
    w("## Per-system summary (mean over windows)")
    w("")
    summary = runs.groupby("system").mean(numeric_only=True).drop(columns=["window", "steps"])
    order = [s for s in SYSTEMS_UNDER_TEST if s in summary.index]
    pretty = summary.loc[order].reset_index()
    pretty["system"] = pretty.system.map(LABELS)
    w(md_table(pretty))
    w("")
    w("## HT-AZ (confirmatory, PREREG_TRACE_AZURE §3) — composite J, paired by window")
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
        w(f"**HT-AZ: PASS with disclosure** — the J wins vs "
          f"{', '.join(LABELS[b] for b in disclosures)} come with a large-effect "
          "violation regression (p<0.01, |d_z|≥0.5): the same "
          "attainment-for-cost trade as on BurstGPT, stated prominently per §3.")
    else:
        w(f"**HT-AZ: {'PASS' if ht_ok else 'FAIL'}** — reported as measured; "
          "stopping rule §4: the tiling exhausts the trace, there is no second "
          "Azure sample either way.")
    w("")
    w("## Declared secondary (estimates, not gates)")
    w("")
    median = float(np.median(share))
    strata = {"conv-dominant windows": np.where(share > median)[0],
              "code-dominant windows": np.where(share <= median)[0]}
    rows = []
    for baseline in HT_BASELINES:
        m = runs[runs.system == "jcac"].merge(
            runs[runs.system == baseline], on="window", suffixes=("_a", "_b"))
        for name, idx in strata.items():
            sub = m[m.window.isin(idx)]
            rows.append({
                "baseline": LABELS[baseline], "stratum": name, "n": len(sub),
                "J diff": float((sub.J_a - sub.J_b).mean()),
                "cost diff (USD)": float((sub.total_cost_usd_a - sub.total_cost_usd_b).mean()),
            })
    w(md_table(pd.DataFrame(rows)))
    w("")
    r = paired(runs, "jcac_v2", "jcac", "J")
    w(f"ET-AZ1 (`jcac_v2` − `jcac` on J): diff {r['mean_diff']:+.4g}, "
      f"p={r['p']:.3g}, d_z={r['dz']:.3f}.")
    w("")
    w("## Notes")
    w("")
    w("- Same substrate rules as the BurstGPT replays: model-scale dollars, the "
      "ranking is the claim, tables never mixed across substrates.")
    w("- Latency metrics are p95, not p99 (documented deviation).")
    w("- 3 h windows cannot contain a daily cycle, so `jcac_seasonal` is "
      "structurally excluded (§2); its Azure evidence is `FORECAST_AZURE.md`.")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--smoke", action="store_true",
                    help="sanctioned discarded pipeline check (prereg §2)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    if args.analyze and OUT_CSV.exists():
        # k and the per-window conv share are the record's only raw-trace
        # inputs; trace_meta freezes them so a clean clone can rebuild it
        # (audit 2026-09-26) and recomputes them wherever the trace exists.
        import trace_meta

        m = trace_meta.load("azure")
        analyze(pd.read_csv(OUT_CSV), np.array(m["conv_share"]), m["k"])
        return

    rates, k = load_rates()
    print(f"windows={N_WINDOWS} x {WINDOW_H}h, k={k:.3f}, "
          f"conv share median={float(np.median(conv_share(rates))):.3f}")

    if args.smoke:
        _init_worker(rates)
        for system in ("jcac", "hpa"):
            row = _run_job((system, 0))
            print(f"smoke {system}: steps={row['steps']} J={row['J']:.4f} "
                  "(discarded per prereg §2)")
        return

    jobs = [(system, widx)
            for widx in range(N_WINDOWS)
            for system in SYSTEMS_UNDER_TEST]
    print(f"{len(jobs)} runs")
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers,
                             initializer=_init_worker, initargs=(rates,)) as pool:
        for i, row in enumerate(pool.map(_run_job, jobs), 1):
            rows.append(row)
            if i % 30 == 0 or i == len(jobs):
                print(f"  [{i}/{len(jobs)}]", flush=True)
    runs = pd.DataFrame(rows).sort_values(["system", "window"])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    runs.to_csv(OUT_CSV, index=False)
    print(f"wrote {OUT_CSV} ({len(runs)} runs)")
    analyze(runs, conv_share(rates), k)


if __name__ == "__main__":
    main()
