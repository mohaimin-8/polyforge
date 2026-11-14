"""The WP11 cells' load schedules, built before any cell runs (programme 2).

Per cell of cells.json, a schedule of real arrivals and real prompts for k6
(load/replay.js), the exact demand the prediction reads, and the knobs:

  arrivals  the cell's stream's real 10 s counts (research/population/
            native.py's counts), superposed native-style but anchored: the
            first day is the day of one evaluation-half window of that stream
            (population.json, split "evaluation"; a seeded choice per cell),
            that window's own 600 s; further whole days follow at the same
            time of day, in a seeded random order, until the count meets the
            target, the last one thinned to exactly the missing number of its
            real arrivals (multivariate hypergeometric). A stream too thin
            for the target takes further passes at other times of the same
            days, as native.series does. Arrivals are uniform within a bin.
            A busy stream (azure2024/conv) is thus a thinned real window, a
            thin one (burstgpt/ChatGPT/conv) a superposition of real days.
  target    N = target_util x C_small(pools[0]) x 600 s / small_share: the
            small pool's offered utilisation is target_util when every
            request is a miss (make_schedule.py's conservative case), with
            C_small(n) the small tier's service rate measured at n replicas
            on the cells' kind of prompts (measure/load_curve.py, its
            mu_trace_rps, in --curves/small_r{n}).
  tenants   two: t00 pinned to the small tier, t01 to mid. Each bin's count
            is split binomially with P(t00) = small_share (seeded); the
            realised split is in demand_10s.csv, which the prediction reads.
  prompts   LMSYS-Chat-1M first user turns in file order from offset 0, the
            same for every cell (make_schedule.take_prompts), t00's chunk
            first; requests in the first WARMUP_S seconds are served and
            cached but not scored.
  cache     the gateway's per-tenant knob (whole MB of completion bytes):
            off 0, small 1, large 16, on both tenants.

Outputs: --out/cellNN/{schedule.json (prompt text: never committed),
schedule.meta.json, demand_10s.csv, pins.json} and, without text, the
request table inputs/cellNN.csv.gz (seq, t, tenant, warm-up, the prompt's row
in WP6's vector cache, its byte length), from which the cache replay and the
prediction are reproduced.

    python cell_schedules.py cells --curves ../out/curves --out ../out/schedules
    python cell_schedules.py calibration-prompts --out ../out/calibration_prompts.json
"""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import io
import json
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
REPO = TESTBED.parent
if str(TESTBED / "load") not in sys.path:
    sys.path.insert(0, str(TESTBED / "load"))

import make_schedule as mk  # noqa: E402

SEED = 20261114
DURATION_S = 600          # one real 600 s window (research/population/features.py)
WARMUP_S = 120
STEPS = DURATION_S // mk.BIN_S
TENANTS = ("t00", "t01")
PINS = {"t00": "small", "t01": "mid"}
CACHE_KNOB_MB = {"off": 0, "small": 1, "large": 16}
MAX_PROMPT_BYTES = 2400   # make_schedule's default: a slot holds 1024 tokens
PROMPT_OFFSET = 0
CALIBRATION_PROMPT_OFFSET = 100_000   # the calibration's prompts are not the cells'
CALIBRATION_PROMPTS = 2000
MAX_PASSES = 50
WP6_VECTORS = REPO / "research" / "cachereplay" / "data" / "vectors" / "gateway"
INPUTS = HERE / "inputs"
TABLE_FIELDS = ("seq", "t", "tenant", "warmup", "vector_row", "prompt_bytes")


def capacity(curves: Path, tier: str, replicas: int) -> float:
    """A tier's measured service rate at `replicas` (trace prompts when measured)."""
    path = curves / f"{tier}_r{replicas}" / "load_curve_meta.json"
    if not path.exists():
        raise FileNotFoundError(f"no load curve for {tier} at {replicas} replicas: {path}")
    return mk.capacity_from(str(path))


def evaluation_windows(population: dict, stream: str) -> list[int]:
    return sorted({int(t["window"]) for w in population["workloads"] if w["split"] == "evaluation"
                   for t in w["tenants"] if t["stream"] == stream})


def stream_days(counts: np.ndarray, day_bins: int) -> np.ndarray:
    """Whole days of a stream's 10 s counts, calendar-indexed (empty days kept)."""
    n = len(counts) // day_bins
    return np.asarray(counts[: n * day_bins], dtype=np.int64).reshape(n, day_bins)


def choose_anchor(windows: Sequence[int], n_days: int, day_bins: int, rng: np.random.Generator) -> tuple[int, int, int]:
    """(window, day, start bin) of one evaluation window inside a whole day."""
    per_window = DURATION_S // mk.BIN_S
    usable = [w for w in windows if (w * per_window) // day_bins < n_days]
    if not usable:
        raise ValueError("no evaluation window of this stream lies in a whole day")
    w = int(usable[int(rng.integers(len(usable)))])
    day, start = divmod(w * per_window, day_bins)
    return w, int(day), int(start)


def anchored_counts(days: np.ndarray, day: int, start: int, steps: int, target: int,
                    rng: np.random.Generator) -> tuple[np.ndarray, int]:
    """`target` real arrivals in `steps` bins: the anchor day's window first,
    then other days at the same start, the last one thinned exactly."""
    n_days, day_bins = days.shape
    acc = np.zeros(steps, dtype=np.int64)
    order = [day] + [int(d) for d in rng.permutation(n_days) if d != day]
    passes = 0
    while acc.sum() < target:
        passes += 1
        if passes > MAX_PASSES:
            raise ValueError("stream too thin for the target")
        for d in order:
            w = days[d, start:start + steps]
            have, add = int(acc.sum()), int(w.sum())
            if add == 0:
                continue
            if have + add <= target:
                acc += w
            else:
                acc += rng.multivariate_hypergeometric(w, target - have)
            if acc.sum() >= target:
                break
        start = int(rng.integers(0, day_bins - steps + 1))  # later passes: other times of the same days
        order = [int(d) for d in rng.permutation(n_days)]
    return acc, passes


def split_counts(counts: np.ndarray, small_share: float, rng: np.random.Generator) -> dict[str, np.ndarray]:
    small = rng.binomial(counts, small_share) if small_share < 1 else counts.copy()
    return {"t00": np.asarray(small, dtype=np.int64), "t01": np.asarray(counts - small, dtype=np.int64)}


def prompt_indices(requests: Sequence[dict], tenant_order: Sequence[str]) -> list[int]:
    """Each request's index into the prompt list, as make_schedule.assign_prompts
    hands them out (contiguous chunks in tenant order, arrival order within)."""
    per = {t: 0 for t in tenant_order}
    for r in requests:
        per[r["tenant"]] += 1
    start, cursor = {}, 0
    for t in tenant_order:
        start[t], cursor = cursor, cursor + per[t]
    used = {t: 0 for t in tenant_order}
    out = []
    for r in requests:
        out.append(start[r["tenant"]] + used[r["tenant"]])
        used[r["tenant"]] += 1
    return out


def wp6_meta_path(lmsys_glob: str = mk.LMSYS_GLOB) -> Path:
    """WP6's vector-cache metadata for the first LMSYS file (file order)."""
    paths = sorted(glob.glob(lmsys_glob))
    if not paths:
        raise FileNotFoundError(f"no LMSYS parquet files match {lmsys_glob}")
    return WP6_VECTORS / f"{Path(paths[0]).stem}.meta.parquet"


def vector_rows(prompt_bytes: np.ndarray, offset: int, count: int, max_bytes: int) -> np.ndarray:
    """Positions in WP6's vector cache of the `count` admissible first turns
    after `offset`: take_prompts' rule applied to the cache's byte lengths."""
    admissible = np.flatnonzero(prompt_bytes <= max_bytes)
    if offset + count > len(admissible):
        raise ValueError("WP6's vector cache does not reach this many prompts")
    return admissible[offset:offset + count]


def request_table(requests: Sequence[dict], rows: np.ndarray, cache_bytes: np.ndarray) -> list[dict]:
    """The schedule without text; refuses a prompt whose byte length differs
    from the vector cache's at its row (the two orders must coincide)."""
    idx = prompt_indices(requests, TENANTS)
    table = []
    for r, k in zip(requests, idx):
        n = len(r["prompt"].encode("utf-8"))
        if n != int(cache_bytes[rows[k]]):
            raise ValueError(f"prompt {k} has {n} bytes, WP6's row {rows[k]} has {cache_bytes[rows[k]]}")
        table.append({"seq": r["seq"], "t": r["t"], "tenant": r["tenant"], "warmup": int(r["warmup"]),
                      "vector_row": int(rows[k]), "prompt_bytes": n})
    return table


def write_table(path: Path, table: Sequence[dict]) -> None:
    """gzip with mtime 0, so the committed bytes depend on the content only."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=TABLE_FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(table)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as f:
        f.write(buf.getvalue().encode("utf-8"))


def read_table(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [{"seq": int(r["seq"]), "t": float(r["t"]), "tenant": r["tenant"], "warmup": r["warmup"] == "1",
                 "vector_row": int(r["vector_row"]), "prompt_bytes": int(r["prompt_bytes"])}
                for r in csv.DictReader(f)]


def build_cell(cell: dict, curves: Path, population: dict, counts_by_stream: dict, day_bins: int,
               prompts_source: Callable[[], Iterable[str]], cache_bytes: np.ndarray | None) -> dict:
    """Everything one cell's run and prediction need; nothing written yet."""
    rng = np.random.default_rng([SEED, cell["cell"]])
    c_small = capacity(curves, "small", cell["pools"][0])
    target = int(round(cell["target_util"] * c_small * DURATION_S / cell["small_share"]))
    days = stream_days(counts_by_stream[cell["stream"]], day_bins)
    window, day, start = choose_anchor(evaluation_windows(population, cell["stream"]), len(days), day_bins, rng)
    counts, passes = anchored_counts(days, day, start, STEPS, target, rng)
    split = split_counts(counts, cell["small_share"], rng)
    arrivals = mk.place_in_bins(split, rng)
    prompts, stats = mk.take_prompts(prompts_source(), PROMPT_OFFSET, len(arrivals), MAX_PROMPT_BYTES)
    requests = mk.assign_prompts(arrivals, TENANTS, prompts)
    for r in requests:
        r["warmup"] = r["t"] < WARMUP_S
    table = None
    if cache_bytes is not None:
        table = request_table(requests, vector_rows(cache_bytes, PROMPT_OFFSET, len(arrivals), MAX_PROMPT_BYTES),
                              cache_bytes)
    load = mk.Load(cell["target_util"], 10.0, c_small, cell["small_share"], 0.0)
    windows = load.windows([a.t for a in arrivals], DURATION_S)
    n = {t: int(split[t].sum()) for t in TENANTS}
    meta = {"schema": mk.SCHEMA, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "wp11-cell", "cell": cell["cell"], "stream": cell["stream"], "duration_s": DURATION_S,
            "steps": STEPS, "bin_s": mk.BIN_S, "warmup_s": WARMUP_S, "requests": len(arrivals),
            "anchor": {"window": window, "day": day, "start_bin": start, "passes": passes},
            "capacity_small_rps": c_small, "target_requests": target,
            "realised_small_share": n["t00"] / max(1, len(arrivals)),
            "utilisation_small": {"mean": load.util(len(arrivals), DURATION_S),
                                  "window_quantiles": {q: float(np.quantile(windows, float(q)))
                                                       for q in ("0.05", "0.5", "0.95", "1.0")}},
            "tenants": [{"id": t, "tier": PINS[t], "stream": cell["stream"], "slo_class": "standard",
                         "requests": n[t]} for t in TENANTS],
            "prompts": {"offset": stats.offset, "used": stats.used, "skipped_long": stats.skipped_long,
                        "max_prompt_bytes": stats.max_prompt_bytes,
                        "source": "LMSYS-Chat-1M first user turns"}}
    pins = {"cell": cell["cell"], "pins": dict(PINS),
            "cache_knob_mb": {t: CACHE_KNOB_MB[cell["cache"]] for t in TENANTS}, "pools": list(cell["pools"])}
    return {"meta": meta, "requests": requests, "demand": mk.demand_rows(arrivals, TENANTS, STEPS),
            "pins": pins, "table": table}


def write_cell(out_dir: Path, built: dict, inputs_dir: Path = INPUTS) -> None:
    mk.write_outputs(out_dir, built["meta"], built["requests"], built["demand"])
    (out_dir / "pins.json").write_text(json.dumps(built["pins"], indent=1), encoding="utf-8")
    if built["table"] is not None:
        write_table(inputs_dir / f"cell{built['meta']['cell']:02d}.csv.gz", built["table"])


def calibration_prompts(out: Path, prompts_source: Callable[[], Iterable[str]] = mk.lmsys_first_turns) -> int:
    prompts, stats = mk.take_prompts(prompts_source(), CALIBRATION_PROMPT_OFFSET, CALIBRATION_PROMPTS,
                                     MAX_PROMPT_BYTES)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": {"offset": stats.offset, "used": stats.used,
                                        "skipped_long": stats.skipped_long, "purpose": "WP11 calibration"},
                               "requests": [{"prompt": p} for p in prompts]}), encoding="utf-8")
    return len(prompts)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cells")
    c.add_argument("--curves", type=Path, required=True, help="dir of small_rN/, mid_rN/ load curves")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--only", type=int, nargs="*", default=None, help="cell numbers (default: all)")
    p = sub.add_parser("calibration-prompts")
    p.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "calibration-prompts":
        print(f"{calibration_prompts(args.out)} prompts -> {args.out}")
        return 0
    import pandas as pd

    native = mk.research_module("native")
    population = json.loads(mk.POPULATION.read_text(encoding="utf-8"))
    cells = json.loads((HERE / "cells.json").read_text(encoding="utf-8"))["cells"]
    wanted = [x for x in cells if args.only is None or x["cell"] in args.only]
    with np.load(native.COUNTS) as z:
        counts = {s: z[s.replace("/", "__")] for s in {x["stream"] for x in wanted}}
    cache_bytes = pd.read_parquet(wp6_meta_path(), columns=["prompt_bytes"])["prompt_bytes"].to_numpy()
    for cell in wanted:
        built = build_cell(cell, args.curves, population, counts, native.DAY_BINS, mk.lmsys_first_turns,
                           cache_bytes)
        write_cell(args.out / f"cell{cell['cell']:02d}", built)
        m = built["meta"]
        print(f"cell {cell['cell']:2d}: {m['requests']} requests, small share {m['realised_small_share']:.3f}, "
              f"small-pool utilisation {m['utilisation_small']['mean']:.3f}, {m['anchor']['passes']} pass(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
