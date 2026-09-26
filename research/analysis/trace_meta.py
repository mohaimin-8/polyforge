"""Trace-derived inputs of the real-trace replay records, frozen in the repo.

The three replay records (RESULTS_TRACE, RESULTS_TRACE2, RESULTS_TRACE_AZURE)
take every statistic from committed run CSVs, but their `--analyze` path
loaded the raw traces (56 MB BurstGPT, 44M-row Azure LLM 2024; neither is
vendored) for three things: the demand scale k, the BurstGPT segment list,
and the per-window Azure stream share. That alone kept all three outside the
reproduction gate (audit 2026-09-26).

`trace_replay_meta.json` freezes those inputs. `load()` recomputes them from
the raw trace whenever it is present and refuses a committed value that
disagrees, so the file cannot drift silently; on a clean clone it reads the
committed value.

    python trace_meta.py --write   # regenerate from the raw traces
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
META = HERE.parents[1] / "eval" / "results" / "trace_replay_meta.json"
SECTIONS = ("burstgpt", "azure")


def _trace(section: str) -> Path:
    if section == "burstgpt":
        import trace_matrix as tm

        return tm.TRACE
    import trace_matrix_azure as ta

    return ta.TRACE


def raw_available(section: str) -> bool:
    return _trace(section).exists()


def compute(section: str) -> dict:
    """The section's inputs, recomputed from its raw trace, JSON-exact."""
    if section == "burstgpt":
        import trace_matrix as tm

        df = tm.load_events()
        segs = tm.segments_of(df)
        return {"k": float(tm.scale_factor(df, segs)),
                "segments": [[int(a), int(b)] for a, b in segs]}
    import trace_matrix_azure as ta

    rates, k = ta.load_rates()
    return {"k": float(k), "conv_share": [float(x) for x in ta.conv_share(rates)]}


def committed() -> dict:
    return json.loads(META.read_text(encoding="utf-8"))


def load(section: str) -> dict:
    if raw_available(section):
        fresh = compute(section)
        if META.exists() and committed().get(section) != fresh:
            raise RuntimeError(
                f"{META.name} is stale for {section}: the raw trace gives "
                "different inputs; regenerate it with `python trace_meta.py --write`")
        return fresh
    if not META.exists():
        raise FileNotFoundError(f"{META.name} missing and the {section} raw trace is absent")
    return committed()[section]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="regenerate from the raw traces")
    args = ap.parse_args()
    if args.write:
        data = {s: compute(s) for s in SECTIONS}
        META.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {META}")
        return 0
    for s in SECTIONS:
        load(s)
        print(f"{s}: committed inputs {'verified against the raw trace' if raw_available(s) else 'read (raw trace absent)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
