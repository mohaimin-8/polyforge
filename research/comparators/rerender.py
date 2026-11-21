"""Rebuild a WP9 campaign's report.md from its committed records (programme 2, WP9; WP16's reproduction).

A campaign directory holds tuned.json (the settings, the classes and each arm's selected
configurations), runs.csv.gz (every tuning and evaluation run) and, when references were asked
for, references.csv.gz. This selects each arm's configurations again from the tuning rows with
protocol.select, refuses when they differ from the ones tuned.json records, and renders report.md
with report.render, as tune.py does after its runs. Nothing is run.

    python rerender.py full/<campaign> [more campaign directories ...]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import protocol  # noqa: E402
import report  # noqa: E402
import tune  # noqa: E402


def records(path: Path) -> list[dict]:
    """A committed CSV's rows as tune.py held them: empty cells left out (J kept, NaN for a failed
    run), granularities a list again, config an int. A missing file has no rows."""
    if not path.exists():
        return []
    out = []
    for rec in pd.read_csv(path, float_precision="round_trip", low_memory=False).to_dict(orient="records"):
        r = {k: v for k, v in rec.items() if k == "J" or not (isinstance(v, float) and math.isnan(v))}
        if "granularities" in r:
            r["granularities"] = [g for g in str(r["granularities"]).split("+") if g]
        if "config" in r:
            r["config"] = int(r["config"])
        out.append(r)
    return out


def _configs_of(selection: dict) -> dict:
    return {arm: {"overall": None if s["overall"] is None else s["overall"].config,
                  "per_class": {c: None if p is None else p.config for c, p in s["per_class"].items()}}
            for arm, s in selection.items()}


def rerender(campaign: Path) -> Path:
    tuned = json.loads((campaign / "tuned.json").read_text(encoding="utf-8"))
    meta = tuned["settings"]
    rows = records(campaign / "runs.csv.gz")
    classes = {w: c for half in tuned["classes"].values() for c, ws in half.items() for w in ws}
    selection = protocol.select([r for r in rows if r["phase"] == "tuning"],
                                {w: classes[w] for w in meta["tuning_workloads"]})
    recorded = {arm: {"overall": (e["overall"] or {}).get("config"),
                      "per_class": {c: (p or {}).get("config") for c, p in e["per_class"].items()}}
                for arm, e in tuned["arms"].items()}
    if _configs_of(selection) != recorded:
        raise SystemExit(f"{campaign}: its tuning rows select other configurations than tuned.json records")
    args = argparse.Namespace(arms=meta["arms"], references=meta["references_requested"],
                              quality_weight=meta["quality_weight"])
    configs = {arm: e["configs"] for arm, e in tuned["arms"].items()}
    text = report.render(meta, tune.arms_meta(args), configs, selection, rows,
                         records(campaign / "references.csv.gz"))
    out = campaign / "report.md"
    out.write_text(text, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("campaigns", nargs="+", type=Path, help="campaign directories (full/<tag>, pilot/<tag>)")
    args = ap.parse_args(argv)
    for campaign in args.campaigns:
        print(rerender(campaign))
    return 0


if __name__ == "__main__":
    sys.exit(main())
