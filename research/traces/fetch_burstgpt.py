"""Fetch + assemble the real BurstGPT v2.0 trace, then normalize it.

The public release (github.com/HPMLL/BurstGPT, v2.0) ships the 10.31M-request,
213-day trace as three CSV parts with the same header. This driver makes the
"on real BurstGPT" runs reproducible in one command:

1. downloads any missing part into research/traces/data/ (gitignored),
2. verifies the parts' timestamp ranges and concatenates them on one clock
   (if a part restarts near zero it is offset to follow its predecessor —
   the release convention is documented per part either way in the output),
3. pushes the assembled frame through the *committed, unchanged*
   etl_burstgpt.normalize() and writes research/traces/out/burstgpt_real.

    python fetch_burstgpt.py            # download if needed + assemble + ETL
    python fetch_burstgpt.py --no-download   # use already-downloaded parts
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import pandas as pd

import common
import etl_burstgpt

DATA = Path(__file__).resolve().parent / "data"
OUT_STEM = str(Path(__file__).resolve().parent / "out" / "burstgpt_real")
RELEASE = "https://github.com/HPMLL/BurstGPT/releases/download/v2.0"
PARTS = ("BurstGPT_1.csv", "BurstGPT_2.csv", "BurstGPT_3.csv")


def fetch(no_download: bool) -> list[Path]:
    DATA.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in PARTS:
        path = DATA / name
        if not path.exists():
            if no_download:
                raise SystemExit(f"missing {path} and --no-download was given")
            print(f"downloading {name} ...")
            urllib.request.urlretrieve(f"{RELEASE}/{name}", path)
        paths.append(path)
    return paths


def assemble(paths: list[Path]) -> pd.DataFrame:
    frames, clock = [], 0.0
    for path in paths:
        df = pd.read_csv(path)
        lo, hi = float(df["Timestamp"].min()), float(df["Timestamp"].max())
        # Parts that restart their clock get offset to follow the previous
        # part; parts already on a continuing clock are left untouched.
        offset = 0.0
        if lo < clock:
            offset = clock - lo + 1.0
            df["Timestamp"] = df["Timestamp"] + offset
        print(f"{path.name}: {len(df)} rows, ts [{lo:.0f}, {hi:.0f}]"
              + (f" -> offset +{offset:.0f}s" if offset else " (continuing clock)"))
        clock = float(df["Timestamp"].max())
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tenants", type=int, default=etl_burstgpt.DEFAULT_TENANTS)
    args = ap.parse_args()

    raw = assemble(fetch(args.no_download))
    days = (raw["Timestamp"].max() - raw["Timestamp"].min()) / etl_burstgpt.SECONDS_PER_DAY
    print(f"assembled: {len(raw)} requests over {days:.0f} days")

    frame = etl_burstgpt.normalize(raw, args.seed, tenants=args.tenants)
    Path(OUT_STEM).parent.mkdir(parents=True, exist_ok=True)
    path = common.write(frame, OUT_STEM)
    print(f"{path}: {len(frame)} events, {frame['tenant_id'].nunique()} tenants, "
          f"hash {common.stream_hash(frame)[:16]}")


if __name__ == "__main__":
    main()
