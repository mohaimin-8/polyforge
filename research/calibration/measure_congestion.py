"""Phase 6 (CPU path): calibrate the simulator's congestion model against a
real inference server.

The sim asserts two latency facts (research/jcac_sim/model.py):
  1. g(rho) = 1/(1-rho) for rho < 0.95 — mean latency inflation vs utilization
  2. P95_FACTOR = 1.4 — p95 ~ mean * 1.4

This script measures both on llama.cpp's llama-server running a real 0.5B
model on this machine's CPU, then reports the measured inflation curve, the
best-fit exponent `a` in g(rho) = (1-rho)^(-a) (the sim claims a = 1), and
the measured p95/mean ratio per load level. Output: CALIBRATION.md +
congestion_runs.csv, both regenerable by rerunning this script.

Protocol (fixed before the first run; a measurement, not a hypothesis test —
no prereg required by the ground rules, but the protocol is still frozen):
  - Server: llama-server, CPU build, --parallel SLOTS slots, fixed threads.
  - Requests are homogeneous by construction (fixed prompt token count, fixed
    n_predict, ignore_eos) to mirror the sim's uniform work units; a nonce
    prefix plus cache_prompt=false defeats prompt caching, so every request
    pays full service cost.
  - Capacity mu: closed-loop at 2*SLOTS in-flight for CAP_SECONDS; mu =
    completed/elapsed. This saturates every slot.
  - Baseline L0: sequential requests (rho ~ 0), mean latency.
  - Sweep: open-loop Poisson arrivals at lambda = rho*mu for each target rho.
    Open-loop is the only regime where rho is controlled by the experimenter;
    closed-loop rho is an outcome, not a factor. All scheduled requests are
    awaited; latency = completion - scheduled arrival (queueing included).
  - Levels run in TWO passes, each pass in a seeded-shuffled order, latencies
    pooled per level; capacity is measured before and after the sweep and
    averaged. Both guards exist because this is a laptop: thermal drift over
    a ~30-minute experiment would otherwise confound with a monotone level
    order (the smoke run showed exactly that).
  - rho >= 0.95 is not measured: the sim's overload branch (quadratic, capped)
    is an optimizer-gradient device, and an open-loop queue at rho ~ 1 has no
    steady state to measure in finite time. The calibration claim covers the
    1/(1-rho) branch only — stated in CALIBRATION.md.

Usage:
  python measure_congestion.py [--quick]
    --quick: shorter windows for a smoke pass (not for the committed numbers).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SERVER_BIN = DATA / "llama-cpp" / "llama-server.exe"
MODEL = DATA / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
PORT = 8987
BASE = f"http://127.0.0.1:{PORT}"

SLOTS = 4          # decode slots; capacity is measured, not assumed
THREADS = 6        # leave 2 of 8 logical cores to the client + OS
N_PREDICT = 48     # fixed completion length -> homogeneous service demand
CTX = 4096
RHO_LEVELS = [0.20, 0.40, 0.60, 0.75, 0.85, 0.92]
SEED = 20260712

PROMPT_BODY = (
    "Summarize the trade-offs between reactive and proactive autoscaling "
    "for multi-tenant model serving, mentioning cost, latency, and fairness "
    "in your answer. Keep the answer factual and complete."
)


def post_completion(nonce: str, timeout: float = 600.0) -> float:
    """One /completion request; returns wall latency in seconds."""
    payload = json.dumps({
        "prompt": f"[req {nonce}] {PROMPT_BODY}",
        "n_predict": N_PREDICT,
        "temperature": 0.0,
        "ignore_eos": True,
        "cache_prompt": False,
    }).encode()
    req = urllib.request.Request(
        BASE + "/completion", data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()
    return time.perf_counter() - t0


def wait_healthy(deadline_s: float = 180.0) -> None:
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(1.0)
    raise RuntimeError("llama-server did not become healthy")


def measure_baseline(n: int) -> list[float]:
    return [post_completion(f"base-{i}") for i in range(n)]


def measure_capacity(seconds: float) -> float:
    """Closed loop at 2*SLOTS in-flight; returns mu in requests/second."""
    stop = time.perf_counter() + seconds
    done = []
    lock = threading.Lock()

    def worker(wid: int) -> None:
        i = 0
        while time.perf_counter() < stop:
            post_completion(f"cap-{wid}-{i}")
            i += 1
            with lock:
                done.append(time.perf_counter())

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2 * SLOTS) as pool:
        list(pool.map(worker, range(2 * SLOTS)))
    elapsed = max(done) - t0 if done else float("nan")
    return len(done) / elapsed


def measure_open_loop(rho: float, mu: float, seconds: float, rng: random.Random) -> list[float]:
    """Poisson arrivals at lambda = rho*mu for `seconds`; awaits every request.
    Latency is measured from *scheduled arrival*, so queueing delay counts —
    that is precisely what g(rho) models."""
    lam = rho * mu
    latencies: list[float] = []
    lock = threading.Lock()

    def fire(nonce: str, scheduled: float) -> None:
        lag = scheduled - time.perf_counter()
        if lag > 0:
            time.sleep(lag)
        post_completion(nonce)  # measured below from `scheduled`
        with lock:
            latencies.append(time.perf_counter() - scheduled)

    t0 = time.perf_counter()
    arrival = t0
    with ThreadPoolExecutor(max_workers=256) as pool:
        i = 0
        while True:
            arrival += rng.expovariate(lam)
            if arrival - t0 > seconds:
                break
            pool.submit(fire, f"r{rho:.2f}-{i}", arrival)
            i += 1
    return latencies


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def fit_exponent(rows: list[dict], l0: float) -> tuple[float, float]:
    """Least squares on log g = -a log(1-rho); returns (a, R^2 of a=1)."""
    xs = [-math.log(1.0 - r["rho_offered"]) for r in rows]
    ys = [math.log(r["mean_s"] / l0) for r in rows]
    a = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
    ss_res_a1 = sum((y - x) ** 2 for x, y in zip(xs, ys))  # sim's model: a=1
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys) or float("nan")
    return a, 1.0 - ss_res_a1 / ss_tot


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    cap_s, level_s, base_n = (30, 45, 5) if args.quick else (90, 120, 20)

    for path in (SERVER_BIN, MODEL):
        if not path.exists():
            raise SystemExit(f"missing {path} — see data/ download commands in CALIBRATION.md")

    server = subprocess.Popen(
        [str(SERVER_BIN), "-m", str(MODEL), "--port", str(PORT),
         "-t", str(THREADS), "--parallel", str(SLOTS), "-c", str(CTX),
         "--no-webui", "--log-disable"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_healthy()
        for i in range(SLOTS):  # warmup: touch every slot once
            post_completion(f"warm-{i}")

        base = measure_baseline(base_n)
        l0 = statistics.fmean(base)
        print(f"baseline L0 = {l0:.3f}s over {base_n} sequential requests")

        mu_pre = measure_capacity(cap_s)
        print(f"capacity mu_pre = {mu_pre:.3f} req/s (closed loop, {2 * SLOTS} in-flight, {cap_s}s)")

        rng = random.Random(SEED)
        pooled: dict[float, list[float]] = {rho: [] for rho in RHO_LEVELS}
        for pass_no in range(2):
            order = list(RHO_LEVELS)
            rng.shuffle(order)
            print(f"pass {pass_no + 1}: order {order}")
            for rho in order:
                lat = measure_open_loop(rho, mu_pre, level_s, rng)
                pooled[rho].extend(lat)
                print(f"  rho={rho:.2f}: n={len(lat)} mean={statistics.fmean(lat):.3f}s")
                time.sleep(5)  # drain/cool between levels

        mu_post = measure_capacity(cap_s)
        print(f"capacity mu_post = {mu_post:.3f} req/s")
        mu = statistics.fmean([mu_pre, mu_post])

        rows = []
        for rho in RHO_LEVELS:
            lat = pooled[rho]
            # rho was offered against mu_pre; restate against the averaged mu.
            rho_eff = rho * mu_pre / mu
            row = {
                "rho_offered": rho_eff, "lambda_rps": rho * mu_pre, "n": len(lat),
                "mean_s": statistics.fmean(lat), "p50_s": statistics.median(lat),
                "p95_s": p95(lat), "g_measured": statistics.fmean(lat) / l0,
                "g_model": 1.0 / (1.0 - rho_eff),
                "p95_over_mean": p95(lat) / statistics.fmean(lat),
            }
            rows.append(row)
            print(f"rho={rho_eff:.2f}: n={row['n']} mean={row['mean_s']:.3f}s "
                  f"g_meas={row['g_measured']:.2f} g_model={row['g_model']:.2f} "
                  f"p95/mean={row['p95_over_mean']:.2f}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()

    a, r2_a1 = fit_exponent(rows, l0)

    csv = HERE / "congestion_runs.csv"
    with csv.open("w", encoding="utf-8", newline="") as f:
        keys = list(rows[0].keys())
        f.write(",".join(keys) + "\n")
        for row in rows:
            f.write(",".join(f"{row[k]}" for k in keys) + "\n")

    md = HERE / "CALIBRATION.md"
    lines = [
        "# Phase 6 calibration — CPU path, as measured",
        "",
        "Generated by `measure_congestion.py` (protocol frozen in its docstring).",
        f"Substrate: llama.cpp llama-server (CPU build), Qwen2.5-0.5B-Instruct Q4_K_M,",
        f"{SLOTS} decode slots, {THREADS} threads, fixed {N_PREDICT}-token completions,",
        "prompt caching defeated. This calibrates the *shape* of the simulator's",
        "latency model against one real inference server; it reruns no campaign and",
        "changes no committed constant. Closed campaign results are untouched.",
        "",
        f"- Baseline unloaded latency L0 = **{l0:.3f} s** ({base_n} sequential requests)",
        f"- Measured capacity mu = **{mu:.3f} req/s** (mean of pre/post: {mu_pre:.3f}/{mu_post:.3f};",
        f"  closed loop, {2 * SLOTS} in-flight, {cap_s} s each). Two shuffled passes per level.",
        "",
        "| rho (offered) | n | mean s | p95 s | g measured | g model 1/(1-rho) | p95/mean |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['rho_offered']:.2f} | {r['n']} | {r['mean_s']:.3f} | {r['p95_s']:.3f} "
            f"| {r['g_measured']:.2f} | {r['g_model']:.2f} | {r['p95_over_mean']:.2f} |")
    ratios = [r["p95_over_mean"] for r in rows]
    lines += [
        "",
        f"**Fit:** g(rho) = (1-rho)^(-a) with **a = {a:.2f}** "
        f"(the sim asserts a = 1; R^2 of the a=1 model on measured points: {r2_a1:.3f}).",
        f"**p95/mean across levels: {min(ratios):.2f}-{max(ratios):.2f}** "
        "(the sim asserts 1.4, flat).",
        "",
        "Scope and honesty:",
        "- One server, one model, one machine, homogeneous requests. This supports",
        "  the *functional form* of g(rho) and P95_FACTOR at decision-quality level;",
        "  it is not a fleet measurement and claims nothing about absolute latencies.",
        "- The load client shares this machine's 8 cores with the server (client",
        "  overhead rides every measured latency); thermal drift is bounded by the",
        "  shuffled two-pass design and the pre/post capacity average, not removed.",
        "- rho >= 0.95 (the sim's capped quadratic overload branch) is deliberately",
        "  not calibrated: an open-loop queue at rho ~ 1 has no finite-time steady",
        "  state; that branch is an optimizer-gradient device, documented as such.",
        "- Committed constants in model.py are unchanged; the committed matrices",
        "  stay bit-reproducible. If a future campaign adopts a recalibrated g, it",
        "  must do so as a new pre-registered experiment.",
        "",
        "GPU path (per-tier latency table): measured — see `TIER_BENCH.md`",
        "and `tier_bench.csv` (`kaggle_tier_bench.py`, free Kaggle GPU kernel).",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {md} and {csv}")


if __name__ == "__main__":
    main()
