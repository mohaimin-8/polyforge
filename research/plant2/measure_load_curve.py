"""Re-measure the CPU server's latency against load, keeping every request
(programme 2, WP2).

`research/calibration/measure_congestion.py` kept only per-level summaries
(six levels), which allows neither a split-half fit with more than three
points per fold nor a bootstrap over requests. This script repeats that
frozen protocol -- same binary, model, 4 slots, 6 threads, 48 forced new
tokens, prompt caching defeated, open-loop Poisson arrivals, two passes in a
seeded shuffled order, capacity measured before and after -- with three
changes, all fixed before the first run:

  1. ten load levels instead of six (rho 0.10 ... 0.90);
  2. every request is written out: level, pass, scheduled arrival, client
     latency from the scheduled arrival, and the server's own timings
     (prompt and decode milliseconds, tokens);
  3. 30 baseline requests instead of 20, and the host's facts beside them.

It writes `data/load_curve_requests.csv`, `data/load_curve_meta.json`. It
changes no committed constant and reruns no campaign.

    python measure_load_curve.py [--quick]
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
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
CAL = HERE.parents[0] / "calibration"
SERVER_BIN = CAL / "data" / "llama-cpp" / "llama-server.exe"
MODEL = CAL / "data" / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
PORT = 8988
BASE = f"http://127.0.0.1:{PORT}"

SLOTS = 4
THREADS = 6
N_PREDICT = 48
CTX = 4096
RHO_LEVELS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90]
SEED = 20261112
PROMPT_BODY = (  # identical to measure_congestion.py
    "Summarize the trade-offs between reactive and proactive autoscaling "
    "for multi-tenant model serving, mentioning cost, latency, and fairness "
    "in your answer. Keep the answer factual and complete."
)
FIELDS = ["phase", "level", "pass_no", "seq", "scheduled_s", "latency_s", "prompt_n",
          "prompt_ms", "predicted_n", "predicted_ms", "error"]


def post_completion(nonce: str, timeout: float = 600.0) -> tuple[float, dict]:
    """One /completion request: (wall seconds from send, server timings)."""
    payload = json.dumps({
        "prompt": f"[req {nonce}] {PROMPT_BODY}", "n_predict": N_PREDICT,
        "temperature": 0.0, "ignore_eos": True, "cache_prompt": False,
    }).encode()
    req = urllib.request.Request(BASE + "/completion", data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    return time.perf_counter() - t0, body.get("timings", {})


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


def record(rows, lock, **kw) -> None:
    with lock:
        rows.append({k: kw.get(k, "") for k in FIELDS})


def _timing_fields(t: dict) -> dict:
    return {"prompt_n": t.get("prompt_n", ""), "prompt_ms": t.get("prompt_ms", ""),
            "predicted_n": t.get("predicted_n", ""), "predicted_ms": t.get("predicted_ms", "")}


def baseline(n: int, rows, lock) -> float:
    lat = []
    for i in range(n):
        s, t = post_completion(f"base-{i}")
        lat.append(s)
        record(rows, lock, phase="baseline", seq=i, latency_s=s, **_timing_fields(t))
    return statistics.fmean(lat)


def capacity(seconds: float, tag: str, rows, lock) -> float:
    stop = time.perf_counter() + seconds
    done: list[float] = []
    t0 = time.perf_counter()

    def worker(wid: int) -> None:
        i = 0
        while time.perf_counter() < stop:
            s, t = post_completion(f"cap{tag}-{wid}-{i}")
            i += 1
            with lock:
                done.append(time.perf_counter())
            record(rows, lock, phase=f"capacity_{tag}", seq=wid * 100000 + i, latency_s=s, **_timing_fields(t))

    with ThreadPoolExecutor(max_workers=2 * SLOTS) as pool:
        list(pool.map(worker, range(2 * SLOTS)))
    return len(done) / (max(done) - t0)


def open_loop(rho: float, mu: float, seconds: float, rng: random.Random, pass_no: int, rows, lock) -> int:
    lam = rho * mu
    t0 = time.perf_counter()

    def fire(seq: int, scheduled: float) -> None:
        lag = scheduled - time.perf_counter()
        if lag > 0:
            time.sleep(lag)
        try:
            _, t = post_completion(f"r{rho:.2f}-p{pass_no}-{seq}")
            record(rows, lock, phase="load", level=rho, pass_no=pass_no, seq=seq,
                   scheduled_s=scheduled - t0, latency_s=time.perf_counter() - scheduled, **_timing_fields(t))
        except (urllib.error.URLError, OSError) as err:
            record(rows, lock, phase="load", level=rho, pass_no=pass_no, seq=seq,
                   scheduled_s=scheduled - t0, error=type(err).__name__)

    arrival, seq = t0, 0
    with ThreadPoolExecutor(max_workers=256) as pool:
        while True:
            arrival += rng.expovariate(lam)
            if arrival - t0 > seconds:
                break
            pool.submit(fire, seq, arrival)
            seq += 1
    return seq


def host_facts() -> dict:
    facts = {"platform": platform.platform(), "processor": platform.processor(),
             "python": platform.python_version()}
    try:
        facts["power_scheme"] = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True,
                                               text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as err:
        facts["power_scheme"] = f"unavailable: {type(err).__name__}"
    return facts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="short smoke pass, not for the record")
    args = ap.parse_args()
    cap_s, level_s, base_n, pause_s = (8, 8, 3, 1) if args.quick else (90, 120, 30, 5)
    for path in (SERVER_BIN, MODEL):
        if not path.exists():
            raise SystemExit(f"missing {path}")
    out_dir = HERE / "data" / ("quick" if args.quick else "")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    lock = threading.Lock()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    server = subprocess.Popen(
        [str(SERVER_BIN), "-m", str(MODEL), "--port", str(PORT), "-t", str(THREADS),
         "--parallel", str(SLOTS), "-c", str(CTX), "--no-webui", "--log-disable"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_healthy()
        for i in range(SLOTS):
            post_completion(f"warm-{i}")
        l0 = baseline(base_n, rows, lock)
        print(f"baseline L0 = {l0:.3f} s ({base_n} sequential)", flush=True)
        mu_pre = capacity(cap_s, "pre", rows, lock)
        print(f"capacity before = {mu_pre:.3f} req/s", flush=True)
        rng = random.Random(SEED)
        for pass_no in range(2):
            order = list(RHO_LEVELS)
            rng.shuffle(order)
            print(f"pass {pass_no + 1}: {order}", flush=True)
            for rho in order:
                n = open_loop(rho, mu_pre, level_s, rng, pass_no, rows, lock)
                print(f"  rho={rho:.2f}: {n} requests", flush=True)
                time.sleep(pause_s)
        mu_post = capacity(cap_s, "post", rows, lock)
        print(f"capacity after = {mu_post:.3f} req/s", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
    with (out_dir / "load_curve_requests.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    meta = {"started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "l0_s": l0,
            "mu_pre_rps": mu_pre, "mu_post_rps": mu_post, "slots": SLOTS, "threads": THREADS,
            "n_predict": N_PREDICT, "levels": RHO_LEVELS, "level_seconds": level_s,
            "capacity_seconds": cap_s, "baseline_requests": base_n, "seed": SEED,
            "rho_offered_against": "mu_pre", "host": host_facts(), "quick": args.quick}
    (out_dir / "load_curve_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
