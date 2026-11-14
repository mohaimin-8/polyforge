"""Repeat the WP2 congestion protocol against one testbed tier (programme 2, WP10).

The protocol is research/plant2/measure_load_curve.py, frozen there: 48 forced
new tokens, prompt caching defeated, an unloaded baseline of 30 sequential
requests, capacity measured closed-loop before and after, ten open-loop
Poisson load levels (rho 0.10 ... 0.90 of the capacity measured before) in two
passes of a seeded shuffled order, every request written out. This port
changes only what the testbed forces:

  1. the server is not spawned: it is a tier's Deployment, reached through
     tier-lb's NodePort (the balanced path the gateway uses), and its slots,
     threads and context are fixed by testbed/k8s/mode-b/llama.yaml;
  2. capacity is driven by 2 x slots x replicas closed-loop workers (the
     original's 2 x slots for its single server), so a tier of n replicas is
     saturated; --phases capacity measures that rate alone, for the curve of
     capacity against replica count;
  3. optionally (--prompts schedule.json) a second capacity, `capacity_trace`:
     the same closed loop with the schedule's real prompts as chat requests
     (no forced length; the server caps at 48 tokens), the rate a trace replay
     should be scaled against (make_schedule.py --capacity-from); with
     --phases all, also `baseline_trace`, the unloaded baseline repeated
     with those prompts (l0_trace_s), so a prediction for real prompts need
     not assume their service time scales like the protocol's.

Outputs keep the original's file names and columns, so research/plant2/fits.py
reads them unchanged:  load_curve_requests.csv, load_curve_meta.json.

    python load_curve.py --tier small --replicas 1 --base-url http://127.0.0.1:30091 \
        --out ../out/curves/small_r1
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

# Frozen protocol constants, identical to research/plant2/measure_load_curve.py.
SLOTS = 4
N_PREDICT = 48
RHO_LEVELS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90]
SEED = 20261112
PROMPT_BODY = (
    "Summarize the trade-offs between reactive and proactive autoscaling "
    "for multi-tenant model serving, mentioning cost, latency, and fairness "
    "in your answer. Keep the answer factual and complete."
)
FIELDS = ["phase", "level", "pass_no", "seq", "scheduled_s", "latency_s", "prompt_n",
          "prompt_ms", "predicted_n", "predicted_ms", "error"]
FULL = {"capacity_s": 90.0, "level_s": 120.0, "baseline_n": 30, "pause_s": 5.0}
QUICK = {"capacity_s": 8.0, "level_s": 8.0, "baseline_n": 3, "pause_s": 1.0}

PostFn = Callable[[str, dict, float], tuple[float, dict]]


def http_post(base_url: str) -> PostFn:
    """POST JSON to base_url + path: (wall seconds, decoded body)."""
    def post(path: str, payload: dict, timeout: float) -> tuple[float, dict]:
        req = urllib.request.Request(base_url.rstrip("/") + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (operator-given URL)
            body = json.loads(resp.read())
        return time.perf_counter() - t0, body
    return post


def protocol_payload(nonce: str) -> dict:
    return {"prompt": f"[req {nonce}] {PROMPT_BODY}", "n_predict": N_PREDICT, "temperature": 0.0,
            "ignore_eos": True, "cache_prompt": False}


def timing_fields(body: dict) -> dict:
    t = body.get("timings", {}) or {}
    return {"prompt_n": t.get("prompt_n", ""), "prompt_ms": t.get("prompt_ms", ""),
            "predicted_n": t.get("predicted_n", ""), "predicted_ms": t.get("predicted_ms", "")}


@dataclass
class Recorder:
    rows: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **kw) -> None:
        with self.lock:
            self.rows.append({k: kw.get(k, "") for k in FIELDS})


@dataclass
class Protocol:
    post: PostFn
    replicas: int
    rec: Recorder
    timeout_s: float = 600.0

    def completion(self, nonce: str) -> tuple[float, dict]:
        return self.post("/completion", protocol_payload(nonce), self.timeout_s)

    def baseline(self, n: int, request: Callable[[str], tuple[float, dict]] | None = None,
                 phase: str = "baseline") -> float:
        """Mean latency of n sequential requests (the protocol's, or `request`'s)."""
        send = request or self.completion
        lat = []
        for i in range(n):
            s, body = send(f"base-{i}")
            lat.append(s)
            self.rec.add(phase=phase, seq=i, latency_s=s, **timing_fields(body))
        return statistics.fmean(lat)

    def capacity(self, seconds: float, tag: str, request: Callable[[str], tuple[float, dict]] | None = None) -> float:
        """Completed requests per second of a closed loop with 2 x slots x replicas workers."""
        send = request or self.completion
        workers = 2 * SLOTS * self.replicas
        stop = time.perf_counter() + seconds
        done: list[float] = []
        t0 = time.perf_counter()

        def worker(wid: int) -> None:
            i = 0
            while time.perf_counter() < stop:
                try:
                    s, body = send(f"cap{tag}-{wid}-{i}")
                except (urllib.error.URLError, OSError) as err:
                    self.rec.add(phase=f"capacity_{tag}", seq=wid * 100000 + i, error=type(err).__name__)
                    i += 1
                    continue
                i += 1
                with self.rec.lock:
                    done.append(time.perf_counter())
                self.rec.add(phase=f"capacity_{tag}", seq=wid * 100000 + i, latency_s=s, **timing_fields(body))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(worker, range(workers)))
        if not done:
            raise RuntimeError(f"capacity_{tag}: no request completed")
        return len(done) / (max(done) - t0)

    def open_loop(self, rho: float, mu: float, seconds: float, rng: random.Random, pass_no: int) -> int:
        """Poisson arrivals at rho x mu for `seconds`; latency from the scheduled arrival."""
        lam = rho * mu
        t0 = time.perf_counter()
        arrivals, arrival = [], t0
        while True:
            arrival += rng.expovariate(lam)
            if arrival - t0 > seconds:
                break
            arrivals.append(arrival)

        def fire(seq: int, scheduled: float) -> None:
            lag = scheduled - time.perf_counter()
            if lag > 0:
                time.sleep(lag)
            try:
                _, body = self.completion(f"r{rho:.2f}-p{pass_no}-{seq}")
                self.rec.add(phase="load", level=rho, pass_no=pass_no, seq=seq, scheduled_s=scheduled - t0,
                             latency_s=time.perf_counter() - scheduled, **timing_fields(body))
            except (urllib.error.URLError, OSError) as err:
                self.rec.add(phase="load", level=rho, pass_no=pass_no, seq=seq, scheduled_s=scheduled - t0,
                             error=type(err).__name__)

        # Every arrival gets a thread, so none waits for a free worker.
        with ThreadPoolExecutor(max_workers=max(32, len(arrivals))) as pool:
            for seq, scheduled in enumerate(arrivals):
                pool.submit(fire, seq, scheduled)
        return len(arrivals)


def trace_requester(post: PostFn, prompts: Sequence[str], timeout_s: float) -> Callable[[str], tuple[float, dict]]:
    """Chat requests cycling through real prompts in order (thread-safe)."""
    lock = threading.Lock()
    state = {"i": 0}

    def send(_nonce: str) -> tuple[float, dict]:
        with lock:
            prompt = prompts[state["i"] % len(prompts)]
            state["i"] += 1
        return post("/v1/chat/completions", {"messages": [{"role": "user", "content": prompt}]}, timeout_s)
    return send


def schedule_prompts(path: str, limit: int = 2000) -> list[str]:
    requests = json.loads(Path(path).read_text(encoding="utf-8"))["requests"]
    prompts = [r["prompt"] for r in requests[:limit]]
    if not prompts:
        raise ValueError(f"{path} holds no requests")
    return prompts


def host_facts() -> dict:
    facts = {"platform": platform.platform(), "processor": platform.processor(),
             "python": platform.python_version()}
    if platform.system() == "Windows":
        try:
            facts["power_scheme"] = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True,
                                                   text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError) as err:
            facts["power_scheme"] = f"unavailable: {type(err).__name__}"
    return facts


def deployment_facts(deployment: str | None, namespace: str, context: str | None) -> dict:
    """The tier's image, args and CPU limit as the cluster reports them."""
    if not deployment:
        return {}
    cmd = ["kubectl", *(["--context", context] if context else []), "-n", namespace,
           "get", "deployment", deployment, "-o", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        obj = json.loads(proc.stdout) if proc.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as err:
        return {"error": type(err).__name__}
    if not obj:
        return {"error": proc.stderr.strip()[:300]}
    c = obj["spec"]["template"]["spec"]["containers"][0]
    return {"deployment": deployment, "spec_replicas": obj["spec"].get("replicas"),
            "ready_replicas": obj.get("status", {}).get("readyReplicas", 0), "image": c.get("image"),
            "args": c.get("args"), "resources": c.get("resources")}


def write_outputs(out_dir: Path, rows: list, meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "load_curve_requests.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    (out_dir / "load_curve_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def run(args: argparse.Namespace, post: PostFn | None = None, pause: Callable[[float], None] = time.sleep,
        timing: dict | None = None) -> dict:
    timing = timing or (QUICK if args.quick else FULL)
    if args.level_seconds:
        timing = {**timing, "level_s": args.level_seconds}
    post = post or http_post(args.base_url)
    rec = Recorder()
    proto = Protocol(post=post, replicas=args.replicas, rec=rec)
    meta: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tier": args.tier,
                  "replicas": args.replicas, "base_url": args.base_url, "slots": SLOTS,
                  "threads": args.threads, "n_predict": N_PREDICT, "seed": SEED, "quick": args.quick,
                  "phases": args.phases, "capacity_workers": 2 * SLOTS * args.replicas,
                  "capacity_seconds": timing["capacity_s"], "host": host_facts(),
                  "cluster": deployment_facts(args.deployment, args.namespace, args.context),
                  "protocol": "research/plant2/measure_load_curve.py, ported (see this file's docstring)"}
    for i in range(SLOTS * args.replicas):
        proto.completion(f"warm-{i}")
    if args.phases == "all":
        meta["l0_s"] = proto.baseline(timing["baseline_n"])
        meta["baseline_requests"] = timing["baseline_n"]
    meta["mu_pre_rps"] = proto.capacity(timing["capacity_s"], "pre")
    if args.phases == "all":
        rng = random.Random(SEED)
        meta.update(levels=RHO_LEVELS, level_seconds=timing["level_s"], rho_offered_against="mu_pre")
        for pass_no in range(2):
            order = list(RHO_LEVELS)
            rng.shuffle(order)
            for rho in order:
                proto.open_loop(rho, meta["mu_pre_rps"], timing["level_s"], rng, pass_no)
                pause(timing["pause_s"])
        meta["mu_post_rps"] = proto.capacity(timing["capacity_s"], "post")
    else:
        meta["mu_post_rps"] = meta["mu_pre_rps"]
    if args.prompts:
        send = trace_requester(post, schedule_prompts(args.prompts), proto.timeout_s)
        if args.phases == "all":
            meta["l0_trace_s"] = proto.baseline(timing["baseline_n"], request=send, phase="baseline_trace")
        meta["mu_trace_rps"] = proto.capacity(timing["capacity_s"], "trace", request=send)
        meta["trace_prompts"] = args.prompts
    meta["failed_requests"] = sum(1 for r in rec.rows if r["error"])
    meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write_outputs(Path(args.out), rec.rows, meta)
    return meta


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tier", required=True, choices=("small", "mid"))
    ap.add_argument("--replicas", type=int, required=True, help="ready replicas of the tier during the run")
    ap.add_argument("--threads", type=int, default=None, help="cores (= threads) per pod, for the record")
    ap.add_argument("--base-url", required=True, help="tier through tier-lb: :30091 small, :30092 mid")
    ap.add_argument("--out", required=True)
    ap.add_argument("--phases", choices=("all", "capacity"), default="all")
    ap.add_argument("--prompts", default=None, help="schedule.json: also measure capacity on its prompts")
    ap.add_argument("--quick", action="store_true", help="short smoke pass, not for the record")
    ap.add_argument("--level-seconds", type=float, default=None, help="override the 120 s per level")
    ap.add_argument("--deployment", default=None, help="record this Deployment's spec (kubectl)")
    ap.add_argument("--namespace", default="pf-testbed")
    ap.add_argument("--context", default="kind-polyforge-testbed")
    args = ap.parse_args(argv)
    if args.replicas < 1:
        ap.error("--replicas must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    meta = run(args)
    print(f"{args.tier} x{args.replicas}: mu_pre {meta['mu_pre_rps']:.3f} req/s, mu_post "
          f"{meta['mu_post_rps']:.3f}" + (f", mu_trace {meta['mu_trace_rps']:.3f}" if "mu_trace_rps" in meta else "")
          + f", {meta['failed_requests']} failed -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
