"""Fit the latency forms to this project's own measurements (programme 2, WP2).

Two measurement sets exist in `research/calibration/`:

- `congestion_runs.csv`: one CPU llama.cpp server (Qwen2.5-0.5B Q4_K_M,
  4 decode slots), open-loop Poisson load at six utilisations, mean and p95
  latency per level. The unloaded latency L0 and the capacity mu are not
  columns; they follow from them exactly (L0 = mean / g_measured,
  mu = lambda / rho).
- `tier_bench_batched.csv`: static batches of 1, 8, 32, 64 requests with 48
  and 96 new tokens on one T4 (Hugging Face generate), for the small and mid
  tiers; `tier_bench_1gpu.csv`: single-stream latency, TTFT and TPOT of the
  same tiers from a separate run (cross-run validation).

Procedure (plan, WP2): fit each form on half of the design points, predict the
other half, both ways; bootstrap each parameter by resampling the measurement
runs (here the load levels or batch cells: coarse, few points); keep the form
with the lower held-out error.

Token counts were measured with the Qwen2.5 tokenizer (llama-server build
9971, /tokenize and /apply-template). Iteration convention: both engines
emit the first token from the prompt pass, so `k` new tokens are one prefill
iteration and k - 1 decode iterations, i.e. the paper's (n, m = k - 1), c = 1
(no token budget binds at these prompt lengths).

    python fits.py      # writes FITS.md and data/fits.json
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize, nnls

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from queueing import RTParams, service_time, steady_state, work  # noqa: E402

CAL = HERE.parents[0] / "calibration"
N_BOOT = 2000
SEED = 20261112

# Measured token counts (see module docstring).
N_CPU = 48            # congestion prompt with its nonce
N_SINGLE = 37         # single-stream GPU prompt (raw, no template)
N_BATCH_ONE = 43      # a batch of 1 holds the first chat prompt
N_BATCH_PADDED = 46   # batches of 8+ hold all four, left-padded to the longest
CPU_SLOTS = 4
CPU_NEW_TOKENS = 48


# ---------------------------------------------------------------- CPU curve

def cpu_levels() -> pd.DataFrame:
    df = pd.read_csv(CAL / "congestion_runs.csv").sort_values("rho_offered").reset_index(drop=True)
    df["l0"] = df.mean_s / df.g_measured
    df["mu"] = df.lambda_rps / df.rho_offered
    if df.l0.std() > 1e-9 or df.mu.std() > 1e-9:
        raise ValueError("congestion_runs.csv: L0 or mu differs across rows")
    return df


def fit_power_published(df: pd.DataFrame, l0: float, mu: float) -> dict:
    """The published method: L0 and mu measured, a by least squares through
    the origin on log(L/L0) = -a log(1 - rho)."""
    x = -np.log(1.0 - df.lambda_rps / mu)
    y = np.log(df.mean_s / l0)
    return {"a": float((x * y).sum() / (x * x).sum()), "l0": l0, "mu": mu}


def fit_power_free(df: pd.DataFrame, mu: float) -> dict:
    """L0 and a both fitted: log L = log L0 - a log(1 - rho)."""
    x = -np.log(1.0 - df.lambda_rps / mu)
    slope, intercept = np.polyfit(x, np.log(df.mean_s), 1)
    return {"a": float(slope), "l0": float(math.exp(intercept)), "mu": mu}


def predict_power(p: dict, lam) -> np.ndarray:
    return p["l0"] * (1.0 - np.asarray(lam) / p["mu"]) ** (-p["a"])


def _rt_cpu(alpha: float, w_tot: float) -> RTParams:
    """At one fixed (n, m) only alpha and W_tot are identified; carry W_tot in
    beta (gamma = 0), which leaves every tau(i) unchanged."""
    return RTParams(alpha, w_tot / (N_CPU + CPU_NEW_TOKENS - 1), 0.0)


def rt_from_l0_mu(l0: float, mu: float) -> dict:
    """No curve fitting at all: tau(1) = L0 and B / tau(B) = mu fix alpha and
    delta, and the chain then predicts the whole curve."""
    iters = CPU_NEW_TOKENS  # c + m = 1 + (k - 1)
    d = (CPU_SLOTS / (iters * mu) - l0 / iters) / (CPU_SLOTS - 1)
    alpha = l0 / iters - d
    return {"alpha": alpha, "w_tot": d * iters}


def predict_rt_cpu(p: dict, lam) -> np.ndarray:
    params = _rt_cpu(p["alpha"], p["w_tot"])
    m = CPU_NEW_TOKENS - 1
    out = []
    for x in np.atleast_1d(lam):
        try:
            out.append(steady_state(float(x), N_CPU, m, CPU_SLOTS, params).response)
        except ValueError:
            out.append(math.inf)
    return np.array(out)


def fit_rt_cpu(df: pd.DataFrame, start: dict) -> dict:
    """Nelder-Mead on the summed squared relative error of the mean latency,
    as the paper fits (log-parametrised so both stay positive)."""
    obs = df.mean_s.to_numpy()
    lam = df.lambda_rps.to_numpy()

    def loss(z):
        pred = predict_rt_cpu({"alpha": math.exp(z[0]), "w_tot": math.exp(z[1])}, lam)
        if not np.all(np.isfinite(pred)):
            return 1e6
        return float(((pred / obs - 1.0) ** 2).sum())

    z0 = [math.log(max(start["alpha"], 1e-4)), math.log(max(start["w_tot"], 1e-3))]
    res = minimize(loss, z0, method="Nelder-Mead", options={"xatol": 1e-8, "fatol": 1e-12, "maxiter": 4000})
    return {"alpha": math.exp(res.x[0]), "w_tot": math.exp(res.x[1])}


def fit_tail(df: pd.DataFrame, mu: float) -> dict:
    """p95/mean = f0 * (1 - rho)^(-b), least squares in logs."""
    x = -np.log(1.0 - df.lambda_rps / mu)
    b, log_f0 = np.polyfit(x, np.log(df.p95_s / df.mean_s), 1)
    return {"f0": float(math.exp(log_f0)), "b": float(b)}


def predict_tail(p: dict, lam, mu: float) -> np.ndarray:
    return p["f0"] * (1.0 - np.asarray(lam) / mu) ** (-p["b"])


def mare(pred, obs) -> float:
    pred, obs = np.asarray(pred, float), np.asarray(obs, float)
    return float(np.mean(np.abs(pred / obs - 1.0)))


def split_half(df: pd.DataFrame) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    a, b = df.iloc[0::2], df.iloc[1::2]
    return [(a, b), (b, a)]


def cpu_analysis(rng: np.random.Generator) -> dict:
    df = cpu_levels()
    l0, mu = float(df.l0.iloc[0]), float(df.mu.iloc[0])
    lam, obs = df.lambda_rps.to_numpy(), df.mean_s.to_numpy()
    zero = rt_from_l0_mu(l0, mu)
    forms = {
        "power, L0 and mu measured": (lambda d: fit_power_published(d, l0, mu), predict_power),
        "power, L0 fitted": (lambda d: fit_power_free(d, mu), predict_power),
        "Ramani-Tantawi, fitted": (lambda d: fit_rt_cpu(d, zero), predict_rt_cpu),
        "Ramani-Tantawi, from L0 and mu only": (lambda d: zero, predict_rt_cpu),
    }
    out = {"l0_s": l0, "mu_rps": mu, "levels": len(df), "forms": {}}
    for name, (fit, pred) in forms.items():
        full = fit(df)
        held = [mare(pred(fit(tr), te.lambda_rps.to_numpy()), te.mean_s) for tr, te in split_half(df)]
        boots = []
        for _ in range(N_BOOT):
            idx = rng.integers(0, len(df), len(df))
            if len(set(idx)) < 3:
                continue
            boots.append(fit(df.iloc[idx].sort_values("rho_offered")))
        keys = [k for k in full if k not in ("mu", "l0") or name == "power, L0 fitted" and k == "l0"]
        out["forms"][name] = {
            "params": full, "in_sample_mare": mare(pred(full, lam), obs),
            "held_out_mare": float(np.mean(held)),
            "ci95": {k: [float(np.percentile([b[k] for b in boots], q)) for q in (2.5, 97.5)] for k in keys},
            "pred_s": [float(x) for x in pred(full, lam)],
        }
    tail = fit_tail(df, mu)
    held_tail = [mare(predict_tail(fit_tail(tr, mu), te.lambda_rps, mu), te.p95_s / te.mean_s)
                 for tr, te in split_half(df)]
    held_flat = [mare(np.full(len(te), 1.4), te.p95_s / te.mean_s) for _, te in split_half(df)]
    tboots = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(df), len(df))
        if len(set(idx)) >= 3:
            tboots.append(fit_tail(df.iloc[idx], mu))
    out["tail"] = {"params": tail, "held_out_mare": float(np.mean(held_tail)),
                   "published_flat_1.4_held_out_mare": float(np.mean(held_flat)),
                   "ci95": {k: [float(np.percentile([b[k] for b in tboots], q)) for q in (2.5, 97.5)]
                            for k in ("f0", "b")}}
    out["observed"] = {"lambda_rps": [float(x) for x in lam], "mean_s": [float(x) for x in obs],
                       "rho": [float(x) for x in df.rho_offered]}
    return out


# ------------------------------------------------------- GPU static batches

def batch_cells(tier: str) -> pd.DataFrame:
    df = pd.read_csv(CAL / "tier_bench_batched.csv")
    df = df[(df.tier == tier) & (df.batch != "exactness")].copy()
    df["x"] = df.batch.astype(int)
    df["k"] = df.new_tokens.astype(int)
    df["n"] = np.where(df.x == 1, N_BATCH_ONE, N_BATCH_PADDED)
    return df.reset_index(drop=True)


def _design_row(x: int, n: int, k: int) -> list[float]:
    """wall = (c+m)*alpha + x*W_tot, linear in (alpha, beta, gamma); c = 1, m = k - 1."""
    m = k - 1
    w_beta = n + m
    w_gamma = n * (1 + 1) / 2.0 + m * (n + (m + 1) / 2.0)
    return [1 + m, x * w_beta, x * w_gamma]


def fit_batches(df: pd.DataFrame) -> RTParams:
    """Non-negative least squares on relative error."""
    a = np.array([_design_row(int(r.x), int(r.n), int(r.k)) for r in df.itertuples()])
    obs = df.wall_s.to_numpy(float)
    theta, _ = nnls(a / obs[:, None], np.ones(len(obs)))
    return RTParams(*map(float, theta))


def predict_batches(p: RTParams, df: pd.DataFrame) -> np.ndarray:
    return np.array([service_time(int(r.x), int(r.n), int(r.k) - 1, p) for r in df.itertuples()])


def single_stream(p: RTParams, n: int, k: int) -> dict:
    """Mean latency, TTFT (a one-token generation: the prefill pass alone)
    and TPOT as the single-stream bench computes it, (mean - ttft)/(k - 1)."""
    m = k - 1
    mean = service_time(1, n, m, p)
    ttft = p.alpha + work(n, m, 1, p)[0]
    return {"mean_ms": 1000 * mean, "ttft_ms": 1000 * ttft, "tpot_ms": 1000 * (mean - ttft) / m}


def gpu_analysis(rng: np.random.Generator) -> dict:
    single = pd.read_csv(CAL / "tier_bench_1gpu.csv").set_index("tier")
    out = {}
    for tier in ("small", "mid"):
        df = batch_cells(tier)
        full = fit_batches(df)
        folds = [(df[df.x.isin([1, 32])], df[df.x.isin([8, 64])]),
                 (df[df.x.isin([8, 64])], df[df.x.isin([1, 32])])]
        held = [mare(predict_batches(fit_batches(tr), te), te.wall_s) for tr, te in folds]
        boots = []
        for _ in range(N_BOOT):
            idx = rng.integers(0, len(df), len(df))
            sub = df.iloc[idx]
            if sub.x.nunique() >= 2 and sub.k.nunique() >= 2:
                boots.append(fit_batches(sub))
        pred_single = single_stream(full, N_SINGLE, 48)
        meas = single.loc[tier]
        out[tier] = {
            "params_ms": {"alpha": 1000 * full.alpha, "beta": 1000 * full.beta, "gamma": 1000 * full.gamma},
            "ci95_ms": {k: [1000 * float(np.percentile([getattr(b, k) for b in boots], q)) for q in (2.5, 97.5)]
                        for k in ("alpha", "beta", "gamma")},
            "in_sample_mare": mare(predict_batches(full, df), df.wall_s),
            "held_out_mare": float(np.mean(held)),
            "capacity_rps_at_64": 64 / service_time(64, N_BATCH_PADDED, 47, full),
            "measured_rps_at_64": float(df[(df.x == 64) & (df.k == 48)].throughput_rps.iloc[0]),
            "single_stream_pred": pred_single,
            "single_stream_measured": {"mean_ms": float(meas.mean_ms), "ttft_ms": float(meas.ttft_ms),
                                       "tpot_ms": float(meas.tpot_ms)},
        }
    return out


# ------------------------------------------------------------------ report

def report(cpu: dict, gpu: dict) -> str:
    L = ["# Plant 2 latency fits (programme 2, WP2)", "",
         "Generated by `research/plant2/fits.py` from `research/calibration/` data. Every number below "
         "is a fit to this project's own measurements; bootstrap intervals resample the measurement "
         f"cells ({N_BOOT} draws) and are coarse because there are few cells. The rule (plan, WP2): "
         "keep the form with the lower held-out error.", "",
         "## CPU server, latency against load (llama.cpp, Qwen2.5-0.5B Q4_K_M, 4 slots, 48 new tokens)", "",
         f"Measured: L0 = {cpu['l0_s']:.3f} s unloaded, capacity mu = {cpu['mu_rps']:.3f} req/s, "
         f"{cpu['levels']} load levels. Split-half: fit on alternate levels, predict the others, both ways.", "",
         "| form | parameters (95% bootstrap interval) | in-sample error | held-out error |",
         "|---|---|---:|---:|"]
    for name, f in cpu["forms"].items():
        parts = []
        for k, v in f["params"].items():
            if k == "mu" or (k == "l0" and name != "power, L0 fitted"):
                continue
            ci = f["ci95"].get(k)
            unit = " s" if k in ("alpha", "w_tot", "l0") else ""
            ci_txt = f" [{ci[0]:.4g}, {ci[1]:.4g}]" if ci and name != "Ramani-Tantawi, from L0 and mu only" else ""
            parts.append(f"{k} = {v:.4g}{unit}{ci_txt}")
        L.append(f"| {name} | {'; '.join(parts)} | {f['in_sample_mare']:.1%} | {f['held_out_mare']:.1%} |")
    best = min(cpu["forms"], key=lambda k: cpu["forms"][k]["held_out_mare"])
    t = cpu["tail"]
    L += ["", f"Lowest held-out error: **{best}**.", "",
          "Mean latency per level (seconds): observed, then each form's in-sample prediction.", "",
          "| rho | observed | " + " | ".join(cpu["forms"]) + " |",
          "|---:|---:|" + "---:|" * len(cpu["forms"])]
    for i, rho in enumerate(cpu["observed"]["rho"]):
        L.append(f"| {rho:.2f} | {cpu['observed']['mean_s'][i]:.3f} | " +
                 " | ".join(f"{f['pred_s'][i]:.3f}" for f in cpu["forms"].values()) + " |")
    L += ["", f"p95/mean = f0 (1 - rho)^(-b): f0 = {t['params']['f0']:.4f} "
          f"[{t['ci95']['f0'][0]:.3f}, {t['ci95']['f0'][1]:.3f}], b = {t['params']['b']:.4f} "
          f"[{t['ci95']['b'][0]:.3f}, {t['ci95']['b'][1]:.3f}]; held-out error {t['held_out_mare']:.1%} "
          f"against {t['published_flat_1.4_held_out_mare']:.1%} for the published flat 1.4.", "",
          "## GPU tiers, iteration cost (one T4, static batches 1-64, 48 and 96 new tokens)", "",
          "The paper's iteration law T(x) = alpha + x delta, fitted per tier by non-negative least "
          "squares on relative error; held out: fit on batches {1, 32}, predict {8, 64}, and back. "
          "Cross-run check: the fit predicts the single-stream run measured separately "
          "(`tier_bench_1gpu.csv`, a different prompt of 37 tokens).", "",
          "| tier | alpha ms | beta ms/token | gamma ms/token | in-sample | held-out | capacity at batch 64, fit / measured |",
          "|---|---|---|---|---:|---:|---|"]
    for tier, g in gpu.items():
        p, ci = g["params_ms"], g["ci95_ms"]
        L.append(f"| {tier} | {p['alpha']:.2f} [{ci['alpha'][0]:.2f}, {ci['alpha'][1]:.2f}] | "
                 f"{p['beta']:.4f} [{ci['beta'][0]:.4f}, {ci['beta'][1]:.4f}] | "
                 f"{p['gamma']:.2e} [{ci['gamma'][0]:.1e}, {ci['gamma'][1]:.1e}] | "
                 f"{g['in_sample_mare']:.1%} | {g['held_out_mare']:.1%} | "
                 f"{g['capacity_rps_at_64']:.2f} / {g['measured_rps_at_64']:.2f} rps |")
    L += ["", "| tier | single stream, predicted / measured: mean ms | TTFT ms | TPOT ms |", "|---|---|---|---|"]
    for tier, g in gpu.items():
        pr, me = g["single_stream_pred"], g["single_stream_measured"]
        L.append(f"| {tier} | {pr['mean_ms']:.0f} / {me['mean_ms']:.0f} | {pr['ttft_ms']:.1f} / {me['ttft_ms']:.1f} | "
                 f"{pr['tpot_ms']:.2f} / {me['tpot_ms']:.2f} |")
    L += ["", "Caveats: one CPU server and one GPU type; few cells per fit, so the intervals are wide and "
          "the held-out errors rest on three points per fold; the GPU runs are static batches under "
          "Hugging Face generate, not a continuous-batching server, so they fit the iteration law, not "
          "the queue; `large` has no batched measurement (its capacity is a swept factor).", ""]
    return "\n".join(L)


def main() -> int:
    rng = np.random.default_rng(SEED)
    cpu = cpu_analysis(rng)
    gpu = gpu_analysis(rng)
    (HERE / "data").mkdir(exist_ok=True)
    (HERE / "data" / "fits.json").write_text(json.dumps({"cpu": cpu, "gpu": gpu}, indent=2), encoding="utf-8")
    (HERE / "FITS.md").write_text(report(cpu, gpu), encoding="utf-8")
    print(report(cpu, gpu))
    return 0


if __name__ == "__main__":
    sys.exit(main())
