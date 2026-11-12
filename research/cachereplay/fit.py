"""WP6 steps 6-7: convert cache entries to megabytes with measured bytes per
entry, fit h(c) = hmax * c / (c + half) per workload class, policy and tau with
bootstrap intervals over tenants, and write the lookup tables plant 2 reads.

    python fit.py --run out/smoke          # needs replay.py's and gocheck mem's outputs

Bytes per entry, two units:
  resident  heap bytes one entry holds in the gateway process, from
            `gocheck mem` (the real SemanticCache.Store path: 384-d float32
            vector, key text, completion, doc ID, Doc struct, Meta map, index
            map slot, policy entry and accounting maps, allocation rounding):
            measured overhead + the entry's key and completion bytes. This is
            plant 2's unit: its memory cost is cache_mb x price per GB-hour.
  knob      completion bytes only: what the live knob cache_size_mb bounds
            (knobs.go putKnobs -> SetTenantBudgetBytes counts len(completion)).
Both come from the entries resident during the warm half of each replay, so a
policy that keeps long completions (cost-aware) is charged for them.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
MIB = float(1 << 20)
SEED = 20261112
BOOT_REPS = 400
PLANT2_DEFAULT = {"class": "all", "policy": "costaware", "tau": 0.85}


def saturating(x, hmax, half):
    return hmax * x / (x + half)


HALF_GRID = 121  # log-spaced candidate halves per fit


def _profile(x: np.ndarray, y: np.ndarray, log_half: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each candidate half, the least-squares hmax (clipped to [0, 1]) and
    the residual sum of squares: hmax is linear given half."""
    f = x[None, :] / (x[None, :] + np.exp(log_half)[:, None])
    hmax = np.clip((f @ y) / np.einsum("ij,ij->i", f, f), 0.0, 1.0)
    resid = y[None, :] - hmax[:, None] * f
    return hmax, np.einsum("ij,ij->i", resid, resid)


def fit_curve(x: np.ndarray, y: np.ndarray) -> tuple[float, float] | None:
    """Least-squares fit of h = hmax * x / (x + half) with 0 <= hmax <= 1:
    hmax is profiled out (linear given half) and half is searched on a
    log grid from 1/100 of the smallest x to 1000 x the largest, then refined
    on a finer grid around the best point. None when it cannot be fitted
    (fewer than 3 points or no positive hit rate)."""
    ok = np.isfinite(x) & np.isfinite(y) & (x > 0)
    x, y = np.asarray(x, float)[ok], np.asarray(y, float)[ok]
    if len(x) < 3 or y.max() <= 0:
        return None
    grid = np.linspace(math.log(x.min() / 100), math.log(x.max() * 1000), HALF_GRID)
    for _ in range(3):
        hmax, sse = _profile(x, y, grid)
        i = int(np.argmin(sse))
        step = grid[1] - grid[0]
        grid = np.linspace(grid[i] - step, grid[i] + step, 41)
    hmax, sse = _profile(x, y, grid)
    i = int(np.argmin(sse))
    return float(hmax[i]), float(math.exp(grid[i]))


def bytes_model(gomem: dict) -> dict:
    """Per-entry overhead beyond the key and completion bytes, from the Go
    heap measurement on the real length sample."""
    sample = [r for r in gomem["runs"] if r["lengths"] == "sample"]
    minimal = [r for r in gomem["runs"] if r["lengths"] == "minimal"]
    if not sample:
        raise SystemExit("gomem.json has no runs on the length sample")
    over = [r["heap_bytes_per_entry"] - r["mean_key_bytes"] - r["mean_completion_bytes"] for r in sample]
    return {"overhead_bytes": float(np.mean(over)), "overhead_range": [float(min(over)), float(max(over))],
            "minimal_entry_bytes": float(np.mean([r["heap_bytes_per_entry"] for r in minimal])),
            "vector_bytes": 4 * int(gomem["dims"]), "go_version": gomem.get("go_version"),
            "runs": gomem["runs"]}


UNITS = (("mb", "resident_mb"), ("knob_mb", "knob_mb"), ("entries", "entries"))


def group_arrays(g: pd.DataFrame) -> dict:
    """Tenant x bounded-capacity matrices of one (class, policy, tau)."""
    b = g[g.capacity > 0]
    caps = np.array(sorted(b.capacity.unique()))
    piv = {c: b.pivot(index="tenant", columns="capacity", values=c).reindex(columns=caps)
           for c in ("warm_hits", "warm_lookups", "warm_mean_resident", "warm_mean_completion_bytes",
                     "warm_mean_key_bytes", "filled")}
    return {"caps": caps, **{k: v.to_numpy(float) for k, v in piv.items()}}


def pooled_points(a: dict, overhead: float, idx=None) -> dict:
    """Pooled warm hit rate and memory per bounded capacity, over all tenants
    or over a resample of them (row indices, with repeats)."""
    sel = slice(None) if idx is None else idx
    res = a["warm_mean_resident"][sel]
    weight = res.sum(axis=0)
    safe = np.where(weight > 0, weight, 1.0)
    comp = (a["warm_mean_completion_bytes"][sel] * res).sum(axis=0) / safe
    key = (a["warm_mean_key_bytes"][sel] * res).sum(axis=0) / safe
    entries = res.mean(axis=0)
    per_entry = overhead + key + comp
    return {"capacity": a["caps"], "entries": entries,
            "hit_rate": a["warm_hits"][sel].sum(axis=0) / a["warm_lookups"][sel].sum(axis=0),
            "resident_mb": entries * per_entry / MIB, "knob_mb": entries * comp / MIB,
            "bytes_per_entry": per_entry, "completion_bytes_per_entry": comp,
            "filled": a["filled"][sel].min(axis=0) > 0}


def fit_group(g: pd.DataFrame, overhead: float, rng: np.random.Generator, reps: int) -> dict:
    a = group_arrays(g)
    pts = pooled_points(a, overhead)
    use = pts["filled"]
    out = {"points": pd.DataFrame(pts).to_dict(orient="records"), "fitted_on": "filled capacities only"}
    est = {u: fit_curve(pts[col][use], pts["hit_rate"][use]) for u, col in UNITS}
    for u, _ in UNITS:
        out[u] = None if est[u] is None else {"cache_hit_max": est[u][0], "half": est[u][1]}
    tenants = len(a["warm_hits"])
    if tenants < 2 or all(e is None for e in est.values()):
        return out
    boots = {u: [] for u, _ in UNITS}
    for _ in range(reps):
        b = pooled_points(a, overhead, rng.integers(tenants, size=tenants))
        for u, col in UNITS:
            fit = fit_curve(b[col][use], b["hit_rate"][use]) if est[u] else None
            if fit is not None:
                boots[u].append(fit)
    for u, _ in UNITS:
        if boots[u]:
            arr = np.array(boots[u])
            out[u]["cache_hit_max_ci95"] = np.percentile(arr[:, 0], [2.5, 97.5]).tolist()
            out[u]["half_ci95"] = np.percentile(arr[:, 1], [2.5, 97.5]).tolist()
            out[u]["bootstrap_fits"] = len(arr)
    return out


def wrong_hits(precision_path: Path | None) -> dict:
    if precision_path is None or not precision_path.exists():
        return {"note": "precision.json not found; run precision.py"}
    p = json.loads(precision_path.read_text(encoding="utf-8"))
    return {"qqp": p.get("qqp_wrong_hit_rate"), "lmsys_response_agreement": p.get("lmsys_wrong_hit_rate"),
            "notes": p.get("notes")}


def plant2_block(curves: dict) -> dict:
    d = PLANT2_DEFAULT
    c = curves.get(d["class"], {}).get(d["policy"], {}).get(f"{d['tau']:.2f}")
    if not c or not c.get("mb"):
        return {"note": "the default class/policy/tau could not be fitted", **d}
    mb, knob = c["mb"], c.get("knob_mb") or {}
    return {**d, "cache_hit_max": mb["cache_hit_max"], "cache_hit_max_ci95": mb.get("cache_hit_max_ci95"),
            "cache_half_mb": mb["half"], "cache_half_mb_ci95": mb.get("half_ci95"),
            "cache_half_mb_knob_unit": knob.get("half"), "unit": "MiB of resident gateway heap per tenant"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default=str(HERE / "out" / "smoke"), help="replay.py output directory")
    ap.add_argument("--gomem", help="gocheck mem output (default <run>/gomem.json)")
    ap.add_argument("--precision", help="precision.py output (default <run>/precision.json)")
    ap.add_argument("--reps", type=int, default=BOOT_REPS, help="bootstrap resamples of tenants")
    args = ap.parse_args()
    run = Path(args.run)
    rows = pd.read_csv(run / "replay.csv")
    gomem = json.loads(Path(args.gomem or run / "gomem.json").read_text(encoding="utf-8"))
    model = bytes_model(gomem)
    rng = np.random.default_rng(SEED)
    curves: dict = {}
    for (cls, policy, tau), g in rows.groupby(["class", "policy", "tau"], sort=True):
        curves.setdefault(cls, {}).setdefault(policy, {})[f"{tau:.2f}"] = fit_group(
            g, model["overhead_bytes"], rng, args.reps)
    replay_meta = json.loads((run / "replay.json").read_text(encoding="utf-8"))
    out = {"schema": "polyforge.wp6.cache/1", "replay": replay_meta, "bytes_per_entry": model,
           "units": {"mb": "MiB (2^20 bytes) of resident gateway heap per tenant",
                     "knob_mb": "MiB of completion bytes (the live knob's accounting)",
                     "entries": "cache entries per tenant"},
           "curves": curves, "wrong_hit_rate": wrong_hits(Path(args.precision) if args.precision
                                                          else run / "precision.json"),
           "plant2": plant2_block(curves)}
    (run / "plant2_cache.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    from report import fit_markdown  # noqa: PLC0415
    (run / "FIT.md").write_text(fit_markdown(out), encoding="utf-8")
    print(f"wrote {run / 'plant2_cache.json'}: plant2 {out['plant2']}")


if __name__ == "__main__":
    main()
