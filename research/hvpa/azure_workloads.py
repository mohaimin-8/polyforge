"""WP17's two workloads from the Azure Functions 2019 trace (programme 2).

Source: Microsoft Azure Functions Trace 2019 (Shahrad et al., "Serverless in
the Wild", USENIX ATC 2020; https://github.com/Azure/AzurePublicDataset),
CC-BY: per function and day, invocation counts per minute. Rule fixed here,
before the file is read: day 1 (invocations_per_function_md.anon.d01.csv),
HTTP-triggered functions summed per application; of the applications active
(count > 0) in at least 90 % of the day's minutes, the two with the most
invocations; for each, the 60-minute window with the largest total (its peak
hour). Written: data/azure_functions_2019.csv, the two windows' per-minute
counts (no identifiers), which workloads.py scales to a mean of twice the
twin's base rate and spreads evenly over each minute's six 10 s steps.

    python -I azure_workloads.py <path to invocations_per_function_md.anon.d01.csv>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "azure_functions_2019.csv"
MINUTES = [str(m) for m in range(1, 1441)]
ACTIVE_SHARE = 0.9
WINDOW = 60


def select(df: pd.DataFrame) -> list[np.ndarray]:
    http = df[df["Trigger"] == "http"]
    apps = http.groupby("HashApp")[MINUTES].sum()
    active = (apps > 0).mean(axis=1) >= ACTIVE_SHARE
    top = apps[active].sum(axis=1).sort_values(ascending=False).index[:2]
    if len(top) < 2:
        raise ValueError("fewer than two applications meet the activity rule")
    out = []
    for app in top:
        series = apps.loc[app].to_numpy(dtype=float)
        sums = np.convolve(series, np.ones(WINDOW), mode="valid")
        start = int(np.argmax(sums))
        out.append(series[start:start + WINDOW])
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    df = pd.read_csv(args[0], usecols=["HashApp", "Trigger", *MINUTES])
    a, b = select(df)
    OUT.parent.mkdir(exist_ok=True)
    pd.DataFrame({"minute": range(WINDOW), "app1": a.astype(int), "app2": b.astype(int)}).to_csv(OUT, index=False)
    print(f"wrote {OUT}: app1 mean {a.mean():.1f}/min (CV {a.std() / a.mean():.2f}), "
          f"app2 mean {b.mean():.1f}/min (CV {b.std() / b.mean():.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
