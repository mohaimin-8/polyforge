"""Build a k6 replay schedule for the WP10 testbed: real arrival times, real
prompts, scaled to a utilisation target of the small tier (programme 2).

Arrivals, two sources:
  native  population workload popNNN (research/population/data/population.json):
          each tenant's demand from real 10 s counts of its stream, built by
          research/population/native.py exactly as the simulator's `native`
          tenant mix builds it (same per-tenant generator seeds), at
          rate_scale x the tenant's rate. The counts carry no finer time, so
          arrivals are placed uniformly at random inside each 10 s bin.
  trace   one stream of the raw traces (research/population/features.py
          names them: burstgpt/ChatGPT/conv, azure2024/code, ...) over
          [start, start + duration): its real arrival times, thinned exactly
          to the target count, assigned to --tenants tenants at random.
          BurstGPT stamps whole seconds; a uniform offset inside the second is
          added.
Prompts: LMSYS-Chat-1M first user turns in file order (the WP6 stream
  construction), one contiguous chunk per tenant, skipping prompts longer
  than --max-prompt-bytes (a slot holds 1024 tokens; the share skipped is
  recorded).
Scaling: the small tier's offered utilisation is
  U = arrivals x small_share x (1 - expected_hit_rate) / (capacity_rps x seconds),
  capacity_rps being the small tier's measured service rate at its replica
  count (measure/load_curve.py). rate_scale is set for the requested mean U
  (0.3-0.9), then lowered if a 60 s window would exceed --max-window-util.
  small_share = 1 and hit rate 0 (the defaults) are the conservative case:
  every request a miss served by small.
Outputs (directory --out; nothing here is ever committed, prompts are licensed):
  schedule.json       {"meta", "requests": [{"seq","t","tenant","prompt","warmup"}]}
  schedule.meta.json  the meta block alone, no prompt text
  demand_10s.csv      step, tenant, count: the exact demand offered, for the simulator

    python make_schedule.py native --workload pop007 --steps 180 \
        --capacity-from ../out/curves/small_r1/load_curve_meta.json --out ../out/schedules/pop007
"""

from __future__ import annotations

import argparse
import csv
import glob
import importlib.util
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable, Iterator, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
POPULATION_DIR = REPO / "research" / "population"
POPULATION = POPULATION_DIR / "data" / "population.json"
LMSYS_GLOB = str(REPO / "research" / "traces" / "data" / "lmsys-chat-1m" / "data" / "*.parquet")
BIN_S = 10            # the native counts' bin and the simulator's control step
WINDOW_S = 60         # utilisation is checked on 60 s windows
UTIL_BAND = (0.3, 0.9)
MAX_SCALE_ITERATIONS = 8
SCHEMA = "polyforge-wp10-schedule/1"


@dataclass(frozen=True)
class Tenant:
    id: str
    stream: str
    rate_rps: float
    slo_class: str
    seed: int


@dataclass(frozen=True)
class Arrival:
    t: float
    tenant: str


def research_module(name: str) -> ModuleType:
    """A research/population module by file path, as eval/harness loads it."""
    key = f"wp10_population_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, POPULATION_DIR / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load research/population/{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def load_population(workload: str, path: Path = POPULATION) -> list[Tenant]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for w in data["workloads"]:
        if w["workload"] == workload:
            return [Tenant(t["tenant"], t["stream"], float(t["rate_rps"]), t["slo_class"], int(t["seed"]))
                    for t in w["tenants"]]
    raise ValueError(f"unknown population workload {workload!r}")


SeriesFn = Callable[[np.ndarray, float, int, np.random.Generator], tuple[np.ndarray, int]]


def native_counts(tenants: Sequence[Tenant], days: dict, run_seed: int, steps: int, scale: float,
                  series: SeriesFn) -> dict[str, np.ndarray]:
    """Per tenant, request counts per 10 s step: native.series at scale x rate,
    seeded per tenant and built for steps + 1 buckets exactly as
    eval/harness/population_workloads.build builds them (the engine scores a
    step against the next bucket); the first `steps` buckets are replayed, so
    a simulator run of the same workload at the scaled rates and run seed sees
    the same counts."""
    out = {}
    for t in tenants:
        rng = np.random.default_rng([t.seed, run_seed])
        rates, _passes = series(days[t.stream], scale * t.rate_rps, steps + 1, rng)
        out[t.id] = np.rint(np.asarray(rates)[:steps] * BIN_S).astype(np.int64)
    return out


def place_in_bins(counts: dict[str, np.ndarray], rng: np.random.Generator) -> list[Arrival]:
    """Arrivals uniformly at random inside each bin, all tenants, sorted by time."""
    arrivals = []
    for tenant in sorted(counts):
        for step, n in enumerate(counts[tenant]):
            for offset in np.sort(rng.uniform(0.0, BIN_S, int(n))):
                arrivals.append(Arrival(step * BIN_S + float(offset), tenant))
    return sorted(arrivals, key=lambda a: (a.t, a.tenant))


def window_utilisation(times: Sequence[float], duration_s: float, capacity_rps: float,
                       share: float, hit_rate: float) -> np.ndarray:
    """Offered small-tier utilisation of each whole 60 s window (a trailing
    partial window is left out rather than read as a full one)."""
    n_windows = max(1, int(duration_s // WINDOW_S))
    idx = (np.asarray(times, dtype=float) // WINDOW_S).astype(int)
    idx = idx[(idx >= 0) & (idx < n_windows)]
    counts = np.bincount(idx, minlength=n_windows)[:n_windows]
    return counts * share * (1.0 - hit_rate) / (capacity_rps * WINDOW_S)


def mean_utilisation(n_requests: int, duration_s: float, capacity_rps: float, share: float,
                     hit_rate: float) -> float:
    return n_requests * share * (1.0 - hit_rate) / (capacity_rps * duration_s)


@dataclass(frozen=True)
class Load:
    target_util: float
    max_window_util: float
    capacity_rps: float
    small_share: float = 1.0
    expected_hit_rate: float = 0.0

    def validate(self) -> None:
        lo, hi = UTIL_BAND
        if not lo <= self.target_util <= hi:
            raise ValueError(f"target utilisation {self.target_util} is outside the WP10 band {UTIL_BAND}")
        if self.capacity_rps <= 0 or not 0 < self.small_share <= 1 or not 0 <= self.expected_hit_rate < 1:
            raise ValueError("need capacity_rps > 0, 0 < small_share <= 1, 0 <= expected_hit_rate < 1")
        if self.max_window_util < self.target_util:
            raise ValueError("max_window_util must be at least target_util")

    def util(self, n: int, duration_s: float) -> float:
        return mean_utilisation(n, duration_s, self.capacity_rps, self.small_share, self.expected_hit_rate)

    def windows(self, times: Sequence[float], duration_s: float) -> np.ndarray:
        return window_utilisation(times, duration_s, self.capacity_rps, self.small_share, self.expected_hit_rate)


def fit_scale(build: Callable[[float], list[Arrival]], scale0: float, duration_s: float,
              load: Load) -> tuple[float, list[Arrival]]:
    """Lower the scale until the peak 60 s window fits; refuse if the mean then
    leaves the band. `build(scale)` returns the arrivals at that scale."""
    scale = scale0
    for _ in range(MAX_SCALE_ITERATIONS):
        arrivals = build(scale)
        peak = float(load.windows([a.t for a in arrivals], duration_s).max(initial=0.0))
        if peak <= load.max_window_util + 1e-9:
            mean = load.util(len(arrivals), duration_s)
            if mean < UTIL_BAND[0] - 1e-9:
                raise ValueError(f"after capping 60 s windows at {load.max_window_util}, mean utilisation "
                                 f"is {mean:.3f} < {UTIL_BAND[0]}: this window is too bursty for the band; "
                                 "choose another window or seed, or raise --max-window-util")
            return scale, arrivals
        scale *= load.max_window_util / peak * 0.98
    raise ValueError(f"no scale met the 60 s window cap in {MAX_SCALE_ITERATIONS} iterations")


def native_arrivals(tenants: Sequence[Tenant], days: dict, run_seed: int, steps: int, load: Load,
                    series: SeriesFn) -> tuple[float, list[Arrival], dict[str, np.ndarray]]:
    """Native-replay arrivals at the scale meeting `load`; also the counts."""
    duration = steps * BIN_S
    total_rate = sum(t.rate_rps for t in tenants)
    scale0 = load.target_util * load.capacity_rps / (load.small_share * (1 - load.expected_hit_rate) * total_rate)
    holder: dict[str, dict[str, np.ndarray]] = {}

    def build(scale: float) -> list[Arrival]:
        counts = native_counts(tenants, days, run_seed, steps, scale, series)
        holder["counts"] = counts
        return place_in_bins(counts, np.random.default_rng([run_seed, 10]))

    scale, arrivals = fit_scale(build, scale0, duration, load)
    return scale, arrivals, holder["counts"]


def thin_exact(times: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """k of the given arrival times, without replacement, sorted."""
    if k > len(times):
        raise ValueError(f"the window holds {len(times)} arrivals, the target needs {k}: "
                         "pick a busier window or use the native source")
    return np.sort(np.asarray(times)[rng.choice(len(times), size=k, replace=False)])


def trace_arrivals(raw_times: np.ndarray, duration_s: float, n_tenants: int, run_seed: int,
                   load: Load, whole_seconds: bool) -> tuple[float, list[Arrival]]:
    """Arrivals thinned from one real window; tenants assigned at random."""
    rng_jitter = np.random.default_rng([run_seed, 20])
    times = np.asarray(raw_times, dtype=float)
    if whole_seconds:
        times = times + rng_jitter.uniform(0.0, 1.0, len(times))
    times = np.sort(times[(times >= 0) & (times < duration_s)])
    k0 = int(round(load.target_util * load.capacity_rps * duration_s
                   / (load.small_share * (1 - load.expected_hit_rate))))
    ids = [f"t{i:02d}" for i in range(n_tenants)]

    def build(scale: float) -> list[Arrival]:
        rng = np.random.default_rng([run_seed, 21])
        picked = thin_exact(times, int(round(k0 * scale)), rng)
        owners = rng.integers(0, n_tenants, len(picked))
        return [Arrival(float(t), ids[o]) for t, o in zip(picked, owners)]

    scale, arrivals = fit_scale(build, 1.0, duration_s, load)
    return scale * k0 / max(len(times), 1), arrivals


def first_user_turn(conversation: Sequence[dict] | None) -> str | None:
    """The first user message's content, or None when it is empty or absent:
    the rule of research/cachereplay/streams.py (_first_turn)."""
    for msg in conversation or []:
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        return content or None
    return None


def lmsys_first_turns(pattern: str = LMSYS_GLOB) -> Iterator[str]:
    """First user turns of LMSYS-Chat-1M in file order (sorted file names)."""
    import pyarrow.parquet as pq

    paths = sorted(glob.glob(pattern))
    if not paths:
        raise FileNotFoundError(f"no LMSYS parquet files match {pattern}")
    for path in paths:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=4096, columns=["conversation"]):
            for convo in batch.column("conversation").to_pylist():
                turn = first_user_turn(convo)
                if turn is not None:
                    yield turn


@dataclass(frozen=True)
class PromptStats:
    offset: int
    used: int
    skipped_long: int
    max_prompt_bytes: int


def take_prompts(turns: Iterable[str], offset: int, count: int, max_bytes: int) -> tuple[list[str], PromptStats]:
    """`count` turns after the first `offset` admissible ones, skipping turns
    over max_bytes (UTF-8); offset counts admissible turns only."""
    out, seen, skipped = [], 0, 0
    for turn in turns:
        if len(turn.encode("utf-8")) > max_bytes:
            skipped += 1
            continue
        seen += 1
        if seen <= offset:
            continue
        out.append(turn)
        if len(out) == count:
            break
    if len(out) < count:
        raise ValueError(f"only {len(out)} admissible prompts after offset {offset}, need {count}")
    return out, PromptStats(offset=offset, used=count, skipped_long=skipped, max_prompt_bytes=max_bytes)


def assign_prompts(arrivals: Sequence[Arrival], tenant_order: Sequence[str],
                   prompts: Sequence[str]) -> list[dict]:
    """Each tenant takes a contiguous chunk of the prompts, in tenant order, and
    uses it in arrival order: a tenant's stream is a run of real first turns."""
    per_tenant: dict[str, int] = {t: 0 for t in tenant_order}
    for a in arrivals:
        per_tenant[a.tenant] += 1
    start, cursor = {}, 0
    for t in tenant_order:
        start[t], cursor = cursor, cursor + per_tenant[t]
    if cursor != len(prompts):
        raise ValueError(f"{len(prompts)} prompts for {cursor} requests")
    used = {t: 0 for t in tenant_order}
    requests = []
    for seq, a in enumerate(arrivals):
        requests.append({"seq": seq, "t": round(a.t, 6), "tenant": a.tenant,
                         "prompt": prompts[start[a.tenant] + used[a.tenant]]})
        used[a.tenant] += 1
    return requests


def demand_rows(arrivals: Sequence[Arrival], tenant_order: Sequence[str], steps: int) -> list[tuple[int, str, int]]:
    counts = {(s, t): 0 for s in range(steps) for t in tenant_order}
    for a in arrivals:
        counts[(min(int(a.t // BIN_S), steps - 1), a.tenant)] += 1
    return [(s, t, counts[(s, t)]) for s in range(steps) for t in tenant_order]


def capacity_from(path: str) -> float:
    """The small tier's capacity from a load_curve.py meta file: the trace-mix
    rate when measured, else the mean of the protocol's pre and post rates."""
    meta = json.loads(Path(path).read_text(encoding="utf-8"))
    if meta.get("mu_trace_rps"):
        return float(meta["mu_trace_rps"])
    return (float(meta["mu_pre_rps"]) + float(meta["mu_post_rps"])) / 2


def write_outputs(out_dir: Path, meta: dict, requests: list[dict], demand: list[tuple[int, str, int]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "schedule.json").write_text(json.dumps({"meta": meta, "requests": requests}), encoding="utf-8")
    (out_dir / "schedule.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    with (out_dir / "demand_10s.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["step", "tenant", "count"])
        writer.writerows(demand)


def build_meta(args: argparse.Namespace, load: Load, scale: float, arrivals: Sequence[Arrival],
               duration_s: float, tenants: list[dict], prompt_stats: PromptStats) -> dict:
    windows = load.windows([a.t for a in arrivals], duration_s)
    return {
        "schema": SCHEMA, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": args.source, "run_seed": args.run_seed, "duration_s": duration_s,
        "steps": int(duration_s // BIN_S), "bin_s": BIN_S, "warmup_s": args.warmup_s,
        "requests": len(arrivals), "rate_scale": scale, "load": asdict(load),
        "utilisation": {"mean": load.util(len(arrivals), duration_s),
                        "window_s": WINDOW_S,
                        "window_quantiles": {q: float(np.quantile(windows, float(q)))
                                             for q in ("0.05", "0.5", "0.95", "1.0")},
                        "share_of_windows_in_band": float(np.mean((windows >= UTIL_BAND[0])
                                                                  & (windows <= UTIL_BAND[1])))},
        "tenants": tenants, "prompts": {**asdict(prompt_stats), "source": "LMSYS-Chat-1M first user turns"},
        "workload": getattr(args, "workload", None), "stream": getattr(args, "stream", None),
        "start_s": getattr(args, "start_s", None),
    }


def run(args: argparse.Namespace, prompts_source: Callable[[], Iterable[str]] = lmsys_first_turns) -> dict:
    load = Load(args.target_util, args.max_window_util,
                capacity_from(args.capacity_from) if args.capacity_from else args.capacity_rps,
                args.small_share, args.expected_hit_rate)
    load.validate()
    if args.source == "native":
        tenants = load_population(args.workload)
        native = research_module("native")
        scale, arrivals, _counts = native_arrivals(tenants, native.load_days(), args.run_seed, args.steps,
                                                   load, native.series)
        duration = args.steps * BIN_S
        order = [t.id for t in tenants]
        info = [{"id": t.id, "stream": t.stream, "slo_class": t.slo_class,
                 "rate_rps": t.rate_rps * scale} for t in tenants]
    else:
        duration = float(args.duration_s)
        raw, whole = read_trace_window(args.stream, args.start_s, duration)
        scale, arrivals = trace_arrivals(raw, duration, args.tenants, args.run_seed, load, whole)
        order = [f"t{i:02d}" for i in range(args.tenants)]
        info = [{"id": t, "stream": args.stream, "slo_class": "standard"} for t in order]
    prompts, stats = take_prompts(prompts_source(), args.prompt_offset, len(arrivals), args.max_prompt_bytes)
    requests = assign_prompts(arrivals, order, prompts)
    for r in requests:
        r["warmup"] = r["t"] < args.warmup_s
    counts = {t: sum(1 for a in arrivals if a.tenant == t) for t in order}
    for row in info:
        row["requests"] = counts[row["id"]]
    meta = build_meta(args, load, scale, arrivals, duration, info, stats)
    steps = int(math.ceil(duration / BIN_S))
    write_outputs(Path(args.out), meta, requests, demand_rows(arrivals, order, steps))
    return meta


def read_trace_window(stream: str, start_s: float, duration_s: float) -> tuple[np.ndarray, bool]:
    """Arrival times (seconds from the window start) of one stream; DuckDB is
    held to one thread so a schedule build does not compete with a campaign."""
    features = research_module("features")
    con = features.connect()
    con.execute("SET threads = 1")
    sql = features.streams(con).get(stream)
    if sql is None:
        raise ValueError(f"unknown stream {stream!r}")
    rows = con.execute(f"SELECT t - ? FROM ({sql}) WHERE t >= ? AND t < ? ORDER BY t",
                       [start_s, start_s, start_s + duration_s]).fetchall()
    return np.array([r[0] for r in rows], dtype=float), stream.startswith("burstgpt/")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="source", required=True)
    nat = sub.add_parser("native", help="population workload, native replay of real 10 s counts")
    nat.add_argument("--workload", required=True, help="popNNN of population.json")
    nat.add_argument("--steps", type=int, default=180, help="10 s steps (180 = 30 min)")
    tr = sub.add_parser("trace", help="one raw stream window, real arrival times")
    tr.add_argument("--stream", required=True, help="e.g. burstgpt/ChatGPT/conv, azure2024/conv")
    tr.add_argument("--start-s", type=float, required=True, help="window start, seconds from trace start")
    tr.add_argument("--duration-s", type=float, default=1800)
    tr.add_argument("--tenants", type=int, default=4)
    for p in (nat, tr):
        p.add_argument("--run-seed", type=int, default=1)
        cap = p.add_mutually_exclusive_group(required=True)
        cap.add_argument("--capacity-rps", type=float, help="small tier service rate, req/s")
        cap.add_argument("--capacity-from", help="load_curve_meta.json of the small tier")
        p.add_argument("--target-util", type=float, default=0.6)
        p.add_argument("--max-window-util", type=float, default=0.9)
        p.add_argument("--small-share", type=float, default=1.0)
        p.add_argument("--expected-hit-rate", type=float, default=0.0)
        p.add_argument("--prompt-offset", type=int, default=0)
        p.add_argument("--max-prompt-bytes", type=int, default=2400)
        p.add_argument("--warmup-s", type=float, default=120.0, help="leading seconds tagged warm-up")
        p.add_argument("--out", required=True, help="output directory (keep it under testbed/out/)")
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        meta = run(args)
    except (ValueError, FileNotFoundError) as err:
        print(f"make_schedule: {err}", file=sys.stderr)
        return 2
    u = meta["utilisation"]
    print(f"{meta['requests']} requests over {meta['duration_s']:.0f} s, rate_scale {meta['rate_scale']:.4g}, "
          f"mean small-tier utilisation {u['mean']:.3f}, 60 s windows p95 {u['window_quantiles']['0.95']:.3f}, "
          f"{meta['prompts']['skipped_long']} long prompts skipped -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
