"""WP6 steps 1-4: replay LMSYS first user turns through a Python mirror of the
gateway's semantic cache and record hit rates over a grid of capacity C
(entries per tenant) and threshold tau (programme 2).

    python replay.py --preset smoke                 # <= 2,000 prompts, minutes
    python replay.py --preset full --workers 6      # every first turn; see README

Streams: first user turns in file order (the files are shuffled, see
streams.py), split per workload class into contiguous tenant streams of
--stream-length prompts. Every tenant has its own cache partition, as in the
gateway. Each tenant stream is replayed request by request (Lookup; on a miss
Store and evict while over capacity) on a virtual clock of --dt seconds per
request; hit rates are reported over the whole stream and over its second
half (the "warm" half, after the first half filled the cache).
Outputs (no prompt text): replay.csv (per tenant), summary.csv (pooled, with
bootstrap intervals over tenants), replay.json, REPLAY.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from cachesim import NeighborIndex, replay_stream
from engine import store_cost_usd
from replay_report import replay_markdown
from streams import CLASSES, DATA, ENCODER, Encoder, class_masks, doc_tiekeys, load_stream, tenant_streams

HERE = Path(__file__).resolve().parent
SEED = 20261112
BOOT_REPS = 2000
TAUS = (0.75, 0.80, 0.85, 0.90, 0.95)
# Virtual seconds between two requests of one tenant: eviction/bench.go's
# "LMSYS-like inter-arrival: ~1.2s mean" (LMSYS itself has no timestamps).
DT_SECONDS = 1.2
PRESETS = {
    "smoke": {"limit": 2000, "stream_length": 250, "capacities": (4, 8, 16, 32, 64, 128, 0),
              "classes": CLASSES, "threads": 2, "workers": 1},
    "full": {"limit": None, "stream_length": 32768,
             "capacities": tuple(64 * 2 ** i for i in range(9)) + (0,),
             "classes": ("all", "conversation", "code"), "threads": None, "workers": 6},
}


@dataclass(frozen=True)
class Grid:
    policies: tuple
    taus: tuple
    capacities: tuple   # 0 = unbounded (cache.go: a capacity of 0 sets no bound)
    dt: float
    k: int


@dataclass(frozen=True)
class Task:
    cls: str
    tenant: int
    positions: np.ndarray
    completion_bytes: np.ndarray
    key_bytes: np.ndarray
    docs: tuple
    grid: Grid
    vectors_path: str | None = None


def _sweep(vec, nb, task: Task, costs, tiekeys, policy: str, tau: float) -> list[dict]:
    """All capacities for one (policy, tau). Once a capacity saw no eviction,
    every larger one (and unbounded) replays identically, so it is reused."""
    rows, saturated = [], None
    for cap in sorted(task.grid.capacities, key=lambda c: (c == 0, c)):
        t0 = time.perf_counter()
        res = saturated or replay_stream(
            vec, costs, task.completion_bytes, task.key_bytes, threshold=tau, capacity=cap,
            policy=policy, dt=task.grid.dt, warm_start=len(vec) // 2, docs=task.docs,
            tiekeys=tiekeys, neighbors=nb)
        reused = saturated is not None
        if res.evictions == 0:
            saturated = res
        rows.append({"class": task.cls, "tenant": task.tenant, "length": len(vec), "policy": policy,
                     "tau": tau, "capacity": cap, **asdict(res),
                     "filled": bool(cap and res.resident_at_warm_start >= cap),
                     "reused": reused, "seconds": time.perf_counter() - t0})
    return rows


def run_tenant(task: Task, vectors: np.ndarray | None = None) -> list[dict]:
    if vectors is None:
        vectors = np.load(task.vectors_path, mmap_mode="r")
    vec = np.ascontiguousarray(vectors[task.positions], dtype=np.float32)
    t0 = time.perf_counter()
    nb = NeighborIndex(vec, k=task.grid.k)
    nb_seconds = time.perf_counter() - t0
    costs = np.array([store_cost_usd(int(b)) for b in task.completion_bytes])
    tiekeys = doc_tiekeys(task.docs)
    rows = []
    for policy in task.grid.policies:
        for tau in task.grid.taus:
            rows.extend(_sweep(vec, nb, task, costs, tiekeys, policy, tau))
    for r in rows:
        r["neighbor_seconds"] = nb_seconds
    return rows


def build_tasks(stream, classes, stream_length: int, grid: Grid) -> list[Task]:
    masks = class_masks(stream.meta)
    comp = stream.meta["completion_bytes"].to_numpy()
    keyb = stream.meta["key_bytes"].to_numpy()
    docs = stream.meta["doc"].to_numpy()
    tasks = []
    for cls in classes:
        for t, pos in enumerate(tenant_streams(masks[cls], stream_length)):
            tasks.append(Task(cls, t, pos, comp[pos], keyb[pos], tuple(docs[pos]), grid))
    return tasks


def run_tasks(tasks: list[Task], vectors: np.ndarray, workers: int, run_dir: Path) -> list[dict]:
    if workers <= 1:
        return [row for task in tasks for row in run_tenant(task, vectors)]
    path = run_dir / "stream_vectors.f32.npy"
    np.save(path, vectors, allow_pickle=False)
    tasks = [replace(t, vectors_path=str(path)) for t in tasks]
    # Workers inherit the environment at spawn: one BLAS thread pool per worker
    # sized so the workers together do not oversubscribe the cores.
    per_worker = str(max(1, (os.cpu_count() or 1) // workers))
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, per_worker)
    # Largest tenants first, so the pool's tail is short.
    tasks.sort(key=lambda t: -len(t.positions))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return [row for rows in pool.map(run_tenant, tasks) for row in rows]


def bootstrap_rate(hits: np.ndarray, lookups: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    """95% percentile interval of the pooled rate, resampling tenants."""
    if len(hits) < 2:
        return float("nan"), float("nan")
    idx = rng.integers(len(hits), size=(BOOT_REPS, len(hits)))
    rates = hits[idx].sum(axis=1) / lookups[idx].sum(axis=1)
    lo, hi = np.percentile(rates, [2.5, 97.5])
    return float(lo), float(hi)


def pool(rows: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    out = []
    for (cls, policy, tau, cap), g in rows.groupby(["class", "policy", "tau", "capacity"], sort=True):
        g = g.sort_values("tenant")
        wh, wl = g.warm_hits.to_numpy(float), g.warm_lookups.to_numpy(float)
        res = g.warm_mean_resident.to_numpy(float)
        lo, hi = bootstrap_rate(wh, wl, rng)
        out.append({"class": cls, "policy": policy, "tau": tau, "capacity": cap, "tenants": len(g),
                    "hit_rate": g.hits.sum() / g.lookups.sum(), "warm_hit_rate": wh.sum() / wl.sum(),
                    "warm_saved_share": g.warm_hit_completion_bytes.sum() / max(1, g.warm_completion_bytes.sum()),
                    "warm_ci_lo": lo, "warm_ci_hi": hi, "tenant_rates": ";".join(f"{x:.4f}" for x in wh / wl),
                    "mean_resident": res.mean(),
                    "completion_bytes_per_entry": np.average(g.warm_mean_completion_bytes, weights=res)
                    if res.sum() else 0.0,
                    "key_bytes_per_entry": np.average(g.warm_mean_key_bytes, weights=res) if res.sum() else 0.0,
                    "filled_share": float(g.filled.mean()), "evictions": int(g.evictions.sum()),
                    "fallbacks": int(g.fallbacks.sum())})
    return pd.DataFrame(out)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=HERE, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _encoder_factory(threads):
    holder = {}

    def make() -> Encoder:
        if "enc" not in holder:
            holder["enc"] = Encoder(threads=threads)
        return holder["enc"]
    return make


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=sorted(PRESETS), default="smoke")
    ap.add_argument("--limit", type=int, help="first N first turns (default: preset)")
    ap.add_argument("--stream-length", type=int, help="prompts per tenant stream")
    ap.add_argument("--capacities", help="comma list of entries per tenant; 0 = unbounded")
    ap.add_argument("--taus", default=",".join(f"{t:.2f}" for t in TAUS))
    ap.add_argument("--policies", default="costaware,lru", help="costaware is the gateway's")
    ap.add_argument("--classes", help=f"comma list from {','.join(CLASSES)}")
    ap.add_argument("--key-format", default="gateway", choices=("gateway", "raw"))
    ap.add_argument("--dt", type=float, default=DT_SECONDS, help="virtual seconds per request")
    ap.add_argument("--k", type=int, default=64, help="candidate neighbours kept per prompt")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--threads", type=int, help="torch threads for the encoder")
    ap.add_argument("--out", help="output directory (default out/<preset>)")
    return ap.parse_args()


def resolve(args: argparse.Namespace) -> dict:
    p = dict(PRESETS[args.preset])
    for key in ("limit", "stream_length", "workers", "threads"):
        if getattr(args, key) is not None:
            p[key] = getattr(args, key)
    if args.capacities:
        p["capacities"] = tuple(int(x) for x in args.capacities.split(","))
    if args.classes:
        p["classes"] = tuple(args.classes.split(","))
    unknown = set(p["classes"]) - set(CLASSES)
    if unknown:
        raise SystemExit(f"unknown classes {sorted(unknown)}")
    p.update(taus=tuple(float(x) for x in args.taus.split(",")), policies=tuple(args.policies.split(",")),
             key_format=args.key_format, dt=args.dt, k=args.k,
             out=Path(args.out) if args.out else HERE / "out" / args.preset)
    return p


def main() -> None:
    args = parse_args()
    cfg = resolve(args)
    cfg["out"].mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    stream = load_stream(cfg["key_format"], cfg["limit"], _encoder_factory(cfg["threads"]))
    t_load = time.perf_counter() - t0
    grid = Grid(cfg["policies"], cfg["taus"], cfg["capacities"], cfg["dt"], cfg["k"])
    tasks = build_tasks(stream, cfg["classes"], cfg["stream_length"], grid)
    print(f"{len(stream.meta)} first turns ({stream.embedded} embedded in {stream.embed_seconds:.1f}s); "
          f"{len(tasks)} tenant streams of {cfg['stream_length']}", flush=True)
    t1 = time.perf_counter()
    DATA.mkdir(parents=True, exist_ok=True)
    rows = pd.DataFrame(run_tasks(tasks, stream.vectors, cfg["workers"], DATA))
    t_replay = time.perf_counter() - t1
    summary = pool(rows)
    rows.to_csv(cfg["out"] / "replay.csv", index=False)
    summary.to_csv(cfg["out"] / "summary.csv", index=False)
    timings = {"load_and_embed_s": t_load, "embed_s": stream.embed_seconds, "embedded": stream.embedded,
               "replay_s": t_replay, "workers": cfg["workers"], "threads": cfg["threads"]}
    write_lengths(stream, cfg)
    meta = describe(stream, cfg, tasks, timings)
    (cfg["out"] / "replay.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    (cfg["out"] / "REPLAY.md").write_text(replay_markdown(meta, summary), encoding="utf-8")
    print(f"wrote {cfg['out']}: replay {t_replay:.1f}s", flush=True)


def write_lengths(stream, cfg: dict) -> None:
    """Byte lengths (no text) for the Go heap measurement; stays under data/."""
    path = DATA / f"lengths_{cfg['out'].name}.csv"
    stream.meta[["key_bytes", "completion_bytes"]].to_csv(path, index=False)


def describe(stream, cfg: dict, tasks: list[Task], timings: dict) -> dict:
    meta = stream.meta
    masks = class_masks(meta)
    return {
        "work_package": "programme 2, WP6 steps 1-4",
        "commit": _git_commit(), "encoder": ENCODER, "dims": int(stream.vectors.shape[1]),
        "key_format": cfg["key_format"], "first_turns": len(meta),
        "class_sizes": {c: int(m.sum()) for c, m in masks.items()},
        "tenants": {c: sum(t.cls == c for t in tasks) for c in cfg["classes"]},
        "stream_length": cfg["stream_length"], "capacities": cfg["capacities"], "taus": cfg["taus"],
        "policies": cfg["policies"], "dt_seconds": cfg["dt"], "neighbours_k": cfg["k"],
        "mean_bytes": {k: float(meta[k].mean()) for k in ("prompt_bytes", "key_bytes", "completion_bytes")},
        "timings": timings, "cpu_count": os.cpu_count(),
    }


if __name__ == "__main__":
    main()
