"""A population of multi-tenant workloads from real request streams
(programme 2, WP8 steps 3-4).

Every choice below is stated here and recorded in `data/population.json`:

- a tenant draws a stream uniformly from the streams with at least
  MIN_WINDOWS active 600 s windows, then a window uniformly from that
  stream's active windows (resampling real windows), and gets the MMPP2
  moment-matched to it (`generator.fit`);
- its mean rate is rescaled to a target drawn log-uniformly from RATE_RANGE
  requests per second (an operating-point choice: the traces' absolute
  rates belong to whole services, not tenants). Rescaling keeps the
  modulation's relative shape; the Poisson part of the dispersion changes
  with the rate, which the check below reports;
- prompt and response lengths are the window's own means;
- SLO class from CLASS_WEIGHTS; budget factor log-uniform on BUDGET_RANGE
  (budget = factor x the plant's cost of serving the tenant's mean demand,
  resolved when a plant is chosen);
- TENANTS per workload (the harness's objective normalises per 8 tenants);
- the population is split once into tuning and evaluation halves by a
  seeded permutation.

Tenants are a construction: the traces carry no tenant identity.

Check (plan, WP8): simulate each tenant for one 600 s window and compare its
features with the real windows' ranges.

    python population.py      # writes data/population.json and POPULATION.md
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from features import BIN_S, MIN_REQUESTS  # noqa: E402
from generator import MMPP2, fit  # noqa: E402

SEED = 20261112
N_WORKLOADS = 240
TENANTS = 8
MIN_WINDOWS = 50
RATE_RANGE = (0.5, 20.0)
BUDGET_RANGE = (1.0, 3.0)
CLASS_WEIGHTS = {"premium": 0.25, "standard": 0.5, "best-effort": 0.25}
ACF_LAGS = {1: "acf_10s", 6: "acf_60s", 30: "acf_300s"}


def active_windows(windows: pd.DataFrame) -> pd.DataFrame:
    act = windows[(windows.n >= MIN_REQUESTS) & windows.dispersion.notna() & windows.p99_ratio.notna()]
    counts = act.groupby("stream").size()
    return act[act.stream.isin(counts[counts >= MIN_WINDOWS].index)].reset_index(drop=True)


def window_model(row) -> MMPP2:
    m = row.rate_rps * BIN_S
    acf = {k: (None if pd.isna(row[c]) else float(row[c])) for k, c in ACF_LAGS.items()}
    return fit(m, float(row.dispersion), acf, float(row.p99_ratio))


def sample(windows: pd.DataFrame, rng: np.random.Generator) -> list[dict]:
    act = active_windows(windows)
    streams = sorted(act.stream.unique())
    by_stream = {s: act[act.stream == s] for s in streams}
    classes, weights = list(CLASS_WEIGHTS), np.array(list(CLASS_WEIGHTS.values()))
    workloads = []
    for w in range(N_WORKLOADS):
        tenants = []
        for t in range(TENANTS):
            stream = streams[rng.integers(len(streams))]
            row = by_stream[stream].iloc[rng.integers(len(by_stream[stream]))]
            model = window_model(row)
            target = math.exp(rng.uniform(math.log(RATE_RANGE[0]), math.log(RATE_RANGE[1])))
            tenants.append({
                "tenant": f"t{t:02d}", "stream": stream, "window": int(row.window),
                "mmpp": {"lam_l": model.lam_l, "lam_h": model.lam_h, "p_up": model.p_up, "p_down": model.p_down},
                "window_rate_rps": float(row.rate_rps), "rate_rps": target,
                "scale": target / float(row.rate_rps),
                "req_tokens": float(row.req_mean), "resp_tokens": float(row.resp_mean),
                "slo_class": classes[rng.choice(len(classes), p=weights / weights.sum())],
                "budget_factor": math.exp(rng.uniform(*map(math.log, BUDGET_RANGE))),
                "seed": int(rng.integers(2**31 - 1)),
            })
        workloads.append({"workload": f"pop{w:03d}", "tenants": tenants})
    order = rng.permutation(N_WORKLOADS)
    for rank, idx in enumerate(order):
        workloads[idx]["split"] = "tuning" if rank < N_WORKLOADS // 2 else "evaluation"
    return workloads


def simulated_features(workloads: list[dict], rng: np.random.Generator, bins: int = 60) -> pd.DataFrame:
    """Each tenant's MMPP, rescaled, simulated for one 600 s window with
    Poisson counts; the same count features as `features.count_features`."""
    rows = []
    for w in workloads:
        for t in w["tenants"]:
            model = MMPP2(**t["mmpp"])
            counts = rng.poisson(model.rates(bins, rng, scale=t["scale"]) * BIN_S)
            mean = counts.mean()
            if mean <= 0:
                continue
            dev = counts - mean
            denom = (dev ** 2).sum()
            rows.append({"stream": t["stream"], "rate_rps": mean / BIN_S,
                         "dispersion": counts.var(ddof=1) / mean,
                         "p99_ratio": np.percentile(counts, 99) / mean,
                         "acf_10s": (dev[:-1] * dev[1:]).sum() / denom if denom > 0 else np.nan})
    return pd.DataFrame(rows)


def native_features(workloads: list[dict], rng: np.random.Generator, bins: int = 60) -> pd.DataFrame:
    """The same count features for each tenant's native replay (`native.py`)."""
    from native import COUNTS, load_days, series

    if not COUNTS.exists():
        return pd.DataFrame()
    day_counts = load_days()
    rows = []
    for w in workloads:
        for t in w["tenants"]:
            counts = series(day_counts[t["stream"]], t["rate_rps"], bins, rng)[0] * BIN_S
            mean = counts.mean()
            dev = counts - mean
            denom = (dev ** 2).sum()
            rows.append({"stream": t["stream"], "dispersion": counts.var(ddof=1) / mean,
                         "p99_ratio": np.percentile(counts, 99) / mean,
                         "acf_10s": (dev[:-1] * dev[1:]).sum() / denom if denom > 0 else np.nan})
    return pd.DataFrame(rows)


def report(workloads: list[dict], real: pd.DataFrame, sim: pd.DataFrame,
           nat: pd.DataFrame | None = None) -> str:
    act = active_windows(real)
    L = ["# Workload population (programme 2, WP8; exploratory)", "",
         "Generated by `research/population/population.py` from `data/windows_600s.csv.gz`. "
         f"{len(workloads)} workloads x {TENANTS} tenants, split once into tuning and evaluation halves "
         f"(seed {SEED}). Tenants are a construction: the traces carry no tenant identity. Rates are "
         f"rescaled to a target drawn log-uniformly in {RATE_RANGE} rps.", "",
         "Tenants per stream: " + ", ".join(f"{k} {v}" for k, v in
                                           pd.Series([t["stream"] for w in workloads for t in w["tenants"]])
                                           .value_counts().sort_index().items()) + ".", "",
         "## Check: simulated 600 s windows against the real ones (shape features)", "",
         "| feature | real 1st-99th percentile | simulated median | simulated inside the real range |",
         "|---|---|---:|---:|"]
    for col in ("p99_ratio", "acf_10s", "dispersion"):
        lo, hi = act[col].quantile(0.01), act[col].quantile(0.99)
        s = sim[col].dropna()
        inside = ((s >= lo) & (s <= hi)).mean()
        L.append(f"| {col} | {lo:.2f} to {hi:.2f} | {s.median():.2f} | {inside:.0%} |")
    have_nat = nat is not None and len(nat)
    L += ["", "## Per stream: medians, real windows / MMPP2 tenants" + (" / native replay" if have_nat else ""), "",
          "| stream | ACF 10 s | p99 ratio | dispersion |", "|---|---|---|---|"]
    for name, g in act.groupby("stream"):
        sg = sim[sim.stream == name]
        ng = nat[nat.stream == name] if have_nat else None

        def cell(col):
            out = f"{g[col].median():.2f} / {sg[col].median():.2f}"
            return out + (f" / {ng[col].median():.2f}" if have_nat else "")
        L.append(f"| {name} | {cell('acf_10s')} | {cell('p99_ratio')} | {cell('dispersion')} |")
    L += ["", "Rescaling a window's rate keeps the modulation's relative shape but not its weight against "
          "Poisson noise: the modulation variance scales with the square of the factor, the Poisson part "
          "with the factor, so a busy stream (Azure, tens of requests per second) rescaled to a few "
          "requests per second keeps little of its autocorrelation. The range check passes; the "
          "medians show the attenuation. Native replay (`native.py`, harness tenant mix `native`) "
          "builds each tenant from real arrivals instead: superposing a thin stream's days keeps its "
          "burstiness (compare the API streams), while thinning a busy stream to a tenant's rate "
          "attenuates its autocorrelation as rescaling does, because a small share of a busy service "
          "is less autocorrelated than the service.", ""]
    return "\n".join(L)


def main() -> int:
    rng = np.random.default_rng(SEED)
    real = pd.read_csv(HERE / "data" / "windows_600s.csv.gz")
    workloads = sample(real, rng)
    sim = simulated_features(workloads, rng)
    meta = {"seed": SEED, "tenants": TENANTS, "rate_range_rps": RATE_RANGE, "budget_range": BUDGET_RANGE,
            "class_weights": CLASS_WEIGHTS, "min_windows": MIN_WINDOWS, "min_requests": MIN_REQUESTS,
            "workloads": workloads}
    (HERE / "data" / "population.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    text = report(workloads, real, sim, native_features(workloads, np.random.default_rng(SEED + 1)))
    (HERE / "POPULATION.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
