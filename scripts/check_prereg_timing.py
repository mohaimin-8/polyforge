"""Did each pre-registered campaign start running AFTER its protocol existed?

`check_preregs.py` proves a protocol's registered text was not altered after
its RECORD appeared. It never compared the protocol with when the campaign
actually RAN -- and a simulator campaign can run, and a record can be written,
minutes after a protocol lands, or before it. The audit of 2026-09-26 found
two campaigns whose first run preceded their protocol's first commit
(eviction parity by ~37 min, order permutation by ~4 min), and no gate saw it.

For every experiment config that names a PREREG_*.md, this compares:

* the protocol's FIRST commit time (committer clock; the push event that is
  the true anchor lives on GitHub and cannot be recovered from a clone), with
* the campaign's first `runs.recorded_at` -- from its DuckDB when present (the
  archive tier; restore it from Zenodo), else from a committed run export
  that carries the column.

**Reading `recorded_at`.** Harness >= 1.1.0 stores UTC. Rows written by 1.0.0
hold the recording host's LOCAL time, unmarked. A campaign whose evidence
carries a `host_facts.json` from a cloud (`-aws`) host is read as UTC -- the
EC2 images run in UTC. Everything else is read as the author's laptop, UTC+6.
That is the conservative reading: it places the run EARLIER, so an unknown
host can raise a false alarm but can never hide a run that preceded its
protocol.

    python scripts/check_prereg_timing.py

Exits nonzero on a run that preceded its protocol and is not disclosed below,
or on a disclosure that the data no longer supports. A gap under two minutes
is printed as `tight`: legal, but the anchor then rests on the push event.
"""

from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = REPO_ROOT / "eval" / "experiments"
RESULTS = REPO_ROOT / "eval" / "results"
ANALYSIS = REPO_ROOT / "research" / "analysis"

LAPTOP_OFFSET_H = 6        # Asia/Dhaka, no daylight saving
TIGHT_S = 120.0

# Campaigns whose first run is known to precede their protocol's first
# commit. Published here so the gate fails on a NEW case instead of sitting
# permanently red, and so the exception is on the record, not absorbed.
DISCLOSED: dict[str, str] = {
    "matrix_eviction_parity": (
        "first run 2026-08-08 10:49:56 UTC, about 37 min BEFORE commit 33bd83c "
        "(11:26:42 UTC), which added PREREG_EVICTION_PARITY.md together with its "
        "results; this protocol has no pre-run anchor (audit 2026-09-26)"),
    "matrix_order_permutation": (
        "first run 2026-08-08 11:22:46 UTC, about 4 min BEFORE commit 33bd83c "
        "added PREREG_ORDER_PERMUTATION.md, although the protocol says it was "
        "committed and pushed before any run (audit 2026-09-26)"),
}


def git(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=REPO_ROOT, capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout.strip()


def _version(v: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        return (0,)


def host_offset_hours(harness_version: str | None, evidence_dir: Path) -> tuple[int, str]:
    """Hours to SUBTRACT from a stored `recorded_at` to get UTC, and why."""
    if _version(harness_version) >= (1, 1, 0):
        return 0, "UTC (harness >= 1.1.0)"
    # Read as UTC only when EVERY host this campaign's evidence names is a
    # cloud host. One cloud sitting among laptop ones must not make a
    # laptop-recorded first run read six hours late -- that would hide a run
    # that preceded its protocol (review of fe23712). Unreadable facts count
    # as unknown, which keeps the conservative reading.
    oses = []
    for facts in sorted(evidence_dir.glob("**/host_facts.json")) if evidence_dir.exists() else []:
        try:
            oses.append(json.loads(facts.read_text(encoding="utf-8")).get("os", ""))
        except (OSError, ValueError):
            oses.append("")
    if oses and all("-aws" in o for o in oses):
        return 0, "UTC (every host_facts is a cloud host)"
    return LAPTOP_OFFSET_H, "UTC+6 assumed (laptop; conservative)"


def verdict(gap_s: float | None, name: str, disclosed: dict[str, str]) -> str:
    if gap_s is None:
        return "no run data"
    if gap_s < 0:
        return "disclosed" if name in disclosed else "RUN BEFORE PREREG"
    return "tight" if gap_s < TIGHT_S else "ok"


def stale_disclosures(rows: list[dict], disclosed: dict[str, str]) -> list[str]:
    """Disclosed campaigns the data shows ran AFTER their protocol after all.
    One that cannot be judged here (no run data) is not called stale."""
    judged = {r["name"]: r["verdict"] for r in rows}
    return [n for n in disclosed if judged.get(n) in ("ok", "tight")]


def campaigns() -> list[tuple[str, str, Path]]:
    """(experiment name, prereg stem, DuckDB path) per config naming a prereg."""
    import yaml

    out = []
    for cfg in sorted(EXPERIMENTS.glob("*.yaml")):
        text = cfg.read_text(encoding="utf-8")
        named = re.findall(r"PREREG_[A-Z0-9_]+", text)
        if not named:
            continue
        spec = yaml.safe_load(text) or {}
        if "name" not in spec or not (ANALYSIS / f"{named[0]}.md").exists():
            continue
        out.append((spec["name"], named[0], REPO_ROOT / spec.get("output", "")))
    return out


def prereg_first_commit(stem: str) -> int | None:
    stamps = git("log", "--format=%ct", "--", f"research/analysis/{stem}.md").split()
    return min(int(s) for s in stamps) if stamps else None


def _committed_exports() -> dict[str, tuple[str, str]]:
    """experiment -> (earliest recorded_at, its harness_version), from every
    committed run export that carries both columns."""
    import csv

    earliest: dict[str, tuple[str, str]] = {}
    tracked = git("ls-files", "eval/results").splitlines()
    for rel in tracked:
        if "/" in rel[len("eval/results/"):] or not rel.endswith((".csv", ".csv.gz")):
            continue
        path = REPO_ROOT / rel
        opener = gzip.open if rel.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if not {"experiment", "recorded_at"} <= set(reader.fieldnames or ()):
                continue
            for row in reader:
                exp, when = row["experiment"], row["recorded_at"]
                if when and (exp not in earliest or when < earliest[exp][0]):
                    earliest[exp] = (when, row.get("harness_version") or "1.0.0")
    return earliest


def first_run(name: str, db: Path, exports: dict) -> tuple[datetime, str] | None:
    """Earliest stored `recorded_at` (naive) and the harness version that wrote it."""
    if db.suffix == ".duckdb" and db.exists():
        import duckdb

        con = duckdb.connect(str(db), read_only=True)
        try:
            row = con.execute(
                "SELECT recorded_at, harness_version FROM runs WHERE experiment = ? "
                "ORDER BY recorded_at LIMIT 1", [name]).fetchone()
        finally:
            con.close()
        if row:
            return row[0], row[1]
    if name in exports:
        when, version = exports[name]
        return datetime.fromisoformat(when), version
    return None


def audit() -> list[dict]:
    exports = _committed_exports()
    rows = []
    for name, stem, db in campaigns():
        committed = prereg_first_commit(stem)
        run = first_run(name, db, exports)
        gap = basis = None
        if run is not None and committed is not None:
            stored, version = run
            hours, basis = host_offset_hours(version, RESULTS / f"{name}_evidence")
            run_utc = (stored - timedelta(hours=hours)).replace(tzinfo=timezone.utc)
            gap = run_utc.timestamp() - committed
        rows.append({"name": name, "prereg": stem, "gap_s": gap, "basis": basis,
                     "verdict": verdict(gap, name, DISCLOSED)})
    return rows


def main() -> int:
    # A depth-1 clone has one commit, so every prereg's "first commit" would
    # be the checkout and every campaign would look like it ran first. Refuse
    # loudly, as check_preregs.py does, rather than report a false storm.
    if git("rev-parse", "--is-shallow-repository") == "true":
        print("REFUSED: shallow clone -- fetch full history (fetch-depth: 0) "
              "before judging prereg timing")
        return 2
    rows = audit()
    print(f"{'campaign':34s} {'protocol':32s} {'run - prereg':>14s}  verdict")
    for r in sorted(rows, key=lambda r: (r["gap_s"] is None, r["gap_s"] or 0.0)):
        gap = "" if r["gap_s"] is None else f"{r['gap_s'] / 60:+.1f} min"
        print(f"{r['name']:34s} {r['prereg']:32s} {gap:>14s}  {r['verdict']}"
              + (f"  [{r['basis']}]" if r["basis"] else ""))
    bad = [r["name"] for r in rows if r["verdict"] == "RUN BEFORE PREREG"]
    stale = stale_disclosures(rows, DISCLOSED)
    for name in sorted(DISCLOSED):
        print(f"disclosed: {name}: {DISCLOSED[name]}")
    judged = sum(r["verdict"] != "no run data" for r in rows)
    print(f"\n{judged} of {len(rows)} campaigns judged; "
          f"{len(bad)} undisclosed run(s) before protocol; {len(stale)} stale disclosure(s)")
    if bad:
        print("FAIL: first run precedes the protocol's first commit: " + ", ".join(bad))
    if stale:
        print("FAIL: disclosure no longer supported by the data: " + ", ".join(stale))
    return 1 if bad or stale else 0


if __name__ == "__main__":
    sys.exit(main())
