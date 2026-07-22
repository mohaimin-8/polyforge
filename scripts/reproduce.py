"""One-command reproduction of PolyForge's committed measurement records.

Re-derives the generated analysis records (RESULTS.md, ADVANCED.md, and —
when the raw databases are present — RESULTS_V2/V3.md, FAIRNESS_V2.md) and
every figure into a scratch directory, then diffs them against the committed
versions. The committed records are frozen by the pre-registration ground
rules and are never written to.

Tiers, detected automatically per input:

  git      a clean `git clone`: run-level statistics and figures rebuild
           from the committed `eval/results/metrics_*.csv.gz` exports and
           the committed security JSON/CSVs. Timeseries figures (fig09) and
           the v2/v3/VTC records need the raw DuckDBs and are skipped with
           a notice.
  archive  with the Zenodo-archived DuckDB files restored into
           `eval/results/`: everything above plus timeseries figures and
           the v2/v3/fairness records.

Float note: the csv.gz exports round-trip DuckDB float32 columns (the
latency percentiles) at float32 precision (~1e-11 absolute here) — seven
orders of magnitude below the 4-significant-figure reporting precision, so
re-derived tables match the committed records.

Usage (from the repo root):
    python scripts/reproduce.py --check   # inputs + deps + plan, no run
    python scripts/reproduce.py           # rebuild into reproduce_out/ and diff
    python scripts/reproduce.py --out DIR # custom scratch directory
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = REPO_ROOT / "research" / "analysis"
RESULTS = REPO_ROOT / "eval" / "results"
FIGURES = RESULTS / "figures"

# What run_analysis.py can produce, and the raw database each record needs
# beyond the committed exports (None = fully rebuildable from git).
RECORDS = [
    ("RESULTS.md", None),
    ("ADVANCED.md", None),
    ("RESULTS_V2.md", "raw_sim_v2.duckdb"),
    ("FAIRNESS_V2.md", "fairness_v2.duckdb"),
    ("RESULTS_V3.md", "raw_sim_v3.duckdb"),
]

# Figures produced by a standalone campaign script rather than by
# run_analysis.py. Each needs its own campaign database, so a reproduction run
# names the script and the file instead of leaving an unexplained gap in the
# figure count.
CAMPAIGN_FIGURES = {
    "fig18_risk_frontier": (
        "research/analysis/analysis_risk.py", "raw_sim_risk.duckdb"),
    "fig19_risk_budget_frontier": (
        "research/analysis/analysis_risk_budget.py", "raw_sim_risk_budget.duckdb"),
}

CORE_EXPORTS = ["metrics_full.csv.gz", "metrics_ablations.csv.gz",
                "metrics_forecasters.csv.gz", "metrics_realism.csv.gz"]
DEPS = ["duckdb", "pandas", "scipy", "statsmodels", "matplotlib"]


def check_inputs() -> tuple[list[str], list[str]]:
    """Returns (problems, notices). Problems block the git tier."""
    problems, notices = [], []
    for name in CORE_EXPORTS:
        if not (RESULTS / name).exists():
            problems.append(f"missing committed export: eval/results/{name}")
    for mod in DEPS:
        try:
            __import__(mod)
        except ImportError:
            problems.append(f"missing python package: {mod} "
                            "(pip install -r eval/requirements.txt)")
    for record, db in RECORDS:
        if db and not (RESULTS / db).exists():
            notices.append(f"{record}: needs eval/results/{db} "
                           "(Zenodo archive) — will be skipped")
    if not (RESULTS / "raw_sim.duckdb").exists():
        notices.append("fig09_adaptation_trace: needs raw_sim.duckdb "
                       "(timeseries live only in the archive) — will be skipped")
    return problems, notices


def diff_record(name: str, out_dir: Path) -> str:
    committed, rebuilt = ANALYSIS / name, out_dir / name
    if not rebuilt.exists():
        return "skipped (needs archive)" if not_available(name) else "NOT PRODUCED"
    if not committed.exists():
        return "no committed version to compare"
    a = committed.read_text(encoding="utf-8").splitlines()
    b = rebuilt.read_text(encoding="utf-8").splitlines()
    delta = [ln for ln in difflib.unified_diff(a, b, lineterm="")
             if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
    if not delta:
        return "MATCH (byte-identical)"
    (out_dir / f"diff_{name}.txt").write_text(
        "\n".join(difflib.unified_diff(a, b, f"committed/{name}",
                                       f"rebuilt/{name}", lineterm="")),
        encoding="utf-8")
    return f"drift: {len(delta)} changed lines (see diff_{name}.txt)"


def not_available(record: str) -> bool:
    db = dict(RECORDS).get(record)
    return bool(db) and not (RESULTS / db).exists()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="verify inputs and dependencies, print the plan, exit")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "reproduce_out",
                    help="scratch output directory (default: reproduce_out/)")
    args = ap.parse_args()

    problems, notices = check_inputs()
    tier = "archive" if (RESULTS / "raw_sim.duckdb").exists() else "git"
    print(f"tier: {tier}")
    for n in notices:
        print(f"  note: {n}")
    for p in problems:
        print(f"  PROBLEM: {p}")
    if args.check:
        print("check", "FAILED" if problems else "OK")
        return 1 if problems else 0
    if problems:
        return 1

    out_dir = args.out.resolve()
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ,
           "POLYFORGE_ANALYSIS_OUT": str(out_dir),
           "POLYFORGE_FIG_DIR": str(fig_dir)}
    print(f"rebuilding into {out_dir} ...")
    proc = subprocess.run([sys.executable, "run_analysis.py"],
                          cwd=ANALYSIS, env=env)
    if proc.returncode != 0:
        print("run_analysis.py FAILED")
        return proc.returncode

    print("\n=== reproduction report ===")
    for record, _ in RECORDS:
        print(f"  {record:<18} {diff_record(record, out_dir)}")
    rebuilt_figs = sorted(p.stem for p in fig_dir.glob("fig*.pdf"))
    committed_figs = sorted(p.stem for p in FIGURES.glob("fig*.pdf"))
    missing = [f for f in committed_figs if f not in rebuilt_figs]
    print(f"  figures            {len(rebuilt_figs)}/{len(committed_figs)} "
          f"rebuilt as vector PDF + 600-DPI PNG")
    for f in missing:
        if f in CAMPAIGN_FIGURES:
            script, db = CAMPAIGN_FIGURES[f]
            print(f"    not rebuilt: {f} — campaign figure; rebuild with "
                  f"`python {script}` (needs eval/results/{db})")
        elif tier == "git" or f == "fig09_adaptation_trace":
            print(f"    not rebuilt: {f} (needs archive timeseries)")
        else:
            print(f"    not rebuilt: {f}")
    print("committed records and figures were not modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
