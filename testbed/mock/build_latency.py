"""Build the mode A mock's inputs from recorded measurements (WP10).

    # a tier's latency as a lognormal matched to a recorded mean and p95
    python build_latency.py lognormal --mean-ms 1551.3 --p95-ms 1595.1 \
        --source "research/calibration/tier_bench_1gpu.csv small" --out data/latency_small.json
    # a tier's latency as the recorded per-request values themselves
    python build_latency.py samples --csv ../out/requests.csv --column latency_s \
        --where phase=baseline --source "..." --out ../out/latency_small.json
    # an answer store whose completion sizes follow recorded byte lengths
    python build_latency.py answers --csv lengths.csv --column completion_bytes \
        --count 64 --source "..." --out data/answers.json

The answer store holds filler text of the recorded sizes, never recorded text:
the size is what the gateway's cache budget counts, and LMSYS-Chat-1M text may
not be transferred.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Sequence

Z95 = 1.6448536269514722  # standard normal 95th percentile
FILLER = ("This is a stored answer of the PolyForge WP10 mode A mock tier; its length "
          "follows a recorded completion-size distribution and its words carry no meaning. ")
MAX_ANSWER_BYTES = 16384


def lognormal_from_mean_p95(mean_s: float, p95_s: float) -> tuple[float, float]:
    """(mu, sigma) of the lognormal with this mean and 95th percentile.

    ln(p95) - ln(mean) = z*sigma - sigma^2/2; the smaller root is the one with
    p95 above the mean, which needs 1 < p95/mean < exp(z^2/2) (about 3.87).
    """
    if mean_s <= 0 or p95_s <= 0:
        raise ValueError("mean and p95 must be positive")
    gap = math.log(p95_s / mean_s)
    if gap <= 0 or gap >= Z95 * Z95 / 2:
        raise ValueError(f"p95/mean = {p95_s / mean_s:.4f} is outside (1, {math.exp(Z95 * Z95 / 2):.2f}),"
                         " no lognormal fits")
    sigma = Z95 - math.sqrt(Z95 * Z95 - 2 * gap)
    return math.log(mean_s) - sigma * sigma / 2, sigma


def read_column(path: str, column: str, where: Sequence[str] = ()) -> list[float]:
    """Numeric values of `column`, keeping rows that match every key=value filter
    and skipping blank cells."""
    filters = []
    for clause in where:
        key, sep, value = clause.partition("=")
        if not sep:
            raise ValueError(f"filter {clause!r} must be key=value")
        filters.append((key, value))
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise ValueError(f"{path} has no column {column!r}")
        values = [float(row[column]) for row in reader
                  if all(row.get(k) == v for k, v in filters) and (row.get(column) or "").strip()]
    if not values:
        raise ValueError(f"no values of {column!r} in {path} after filters {list(where)}")
    return values


def even_quantiles(values: Sequence[float], count: int) -> list[float]:
    """`count` values at the mid-points of equal-probability bins, linear
    interpolation between order statistics: a deterministic summary."""
    if count < 1:
        raise ValueError("count must be at least 1")
    ordered = sorted(values)
    n = len(ordered)
    out = []
    for i in range(count):
        pos = (i + 0.5) / count * (n - 1)
        lo = math.floor(pos)
        hi = min(lo + 1, n - 1)
        out.append(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo))
    return out


def filler(size: int) -> str:
    """ASCII filler text of exactly `size` bytes (at least one)."""
    size = max(1, min(int(size), MAX_ANSWER_BYTES))
    repeats = size // len(FILLER) + 1
    return (FILLER * repeats)[:size]


def write_json(path: str, payload: dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ln = sub.add_parser("lognormal", help="lognormal matched to a recorded mean and p95")
    ln.add_argument("--mean-ms", type=float, required=True)
    ln.add_argument("--p95-ms", type=float, required=True)
    sm = sub.add_parser("samples", help="the recorded per-request latencies themselves")
    sm.add_argument("--csv", required=True)
    sm.add_argument("--column", required=True)
    sm.add_argument("--where", action="append", default=[], help="key=value row filter")
    sm.add_argument("--unit", choices=("s", "ms"), default="s")
    an = sub.add_parser("answers", help="answer store with recorded completion sizes")
    an.add_argument("--csv", required=True)
    an.add_argument("--column", required=True)
    an.add_argument("--where", action="append", default=[])
    an.add_argument("--count", type=int, default=64)
    for p in (ln, sm, an):
        p.add_argument("--source", required=True, help="where the recorded numbers come from")
        p.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "lognormal":
        mu, sigma = lognormal_from_mean_p95(args.mean_ms / 1000, args.p95_ms / 1000)
        payload = {"kind": "lognormal", "mu": mu, "sigma": sigma, "unit": "s",
                   "matched": {"mean_s": args.mean_ms / 1000, "p95_s": args.p95_ms / 1000},
                   "source": args.source}
    elif args.cmd == "samples":
        factor = 0.001 if args.unit == "ms" else 1.0
        values = [v * factor for v in read_column(args.csv, args.column, args.where)]
        payload = {"kind": "samples", "values_s": values, "unit": "s", "n": len(values),
                   "source": args.source}
    else:
        sizes = [round(v) for v in even_quantiles(read_column(args.csv, args.column, args.where), args.count)]
        payload = {"answers": [filler(s) for s in sizes], "sizes_bytes": sizes,
                   "note": "filler text of recorded completion sizes; no recorded text",
                   "source": args.source}
    write_json(args.out, payload)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
