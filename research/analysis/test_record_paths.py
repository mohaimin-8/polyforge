"""No generator may write a record to its own directory.

`stats.record_path` exists so `scripts/reproduce.py` can point a generator at
a scratch directory and compare what comes out. A script that resolves its
output against `Path(__file__).parent` instead has two faults at once: running
it OVERWRITES the committed record, and the gate cannot regenerate it to
compare -- so the record silently leaves the gate's coverage.

Session 43 found seventeen scripts in that state, and the consequences were
not hypothetical. `analysis_vtc.py` regenerated `VTC_FAIRNESS.md` as a table
of zeros on a clean clone, and two of them rewrote committed CSVs on every
reproduce run, under a gate that prints "committed records and figures were
not modified".

Finding them took a manual sweep, twice, because each fix revealed another.
This test is that sweep, run automatically.

    python -m pytest research/analysis/test_record_paths.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

# `Path(__file__)...parent / "something.md"` and the same for .csv/.json.
SELF_DIR = re.compile(
    r'Path\(__file__\)\.resolve\(\)\.parents?(?:\[0\])?\s*\.?\s*'
    r'(?:parent\s*)?/\s*(?:f)?"([^"]+\.(?:md|csv|json))"')

# Each exception carries its reason, so the list cannot quietly become a place
# to hide a new offender.
ALLOWED = {
    ("planner_cells_dealias.py", "PLANNER_CELLS.md"):
        "a READ, not a write: this generator quotes the frozen earlier record "
        "as input, and it must resolve to the committed copy rather than to a "
        "scratch directory",
    ("separation_mt_v3_walk.py", "separation_mt_v3_walk.json"):
        "the DEFAULT for an explicit --out. This is the two-hour ordered walk "
        "that PRODUCES a committed input artifact rather than a record the "
        "gate regenerates; when it is deliberately re-run, writing beside the "
        "record is the intended destination",
}


def test_no_generator_writes_into_its_own_directory():
    offenders = []
    for path in sorted(HERE.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            m = SELF_DIR.search(line)
            if not m:
                continue
            if (path.name, m.group(1)) in ALLOWED:
                continue
            offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        "these resolve an output against the script's own directory instead of "
        "stats.record_path, so running them overwrites the committed file and "
        "the reproduction gate cannot compare it:\n  " + "\n  ".join(offenders))


def test_every_exception_still_applies():
    """An allowlist nobody re-checks becomes a place to hide an offender."""
    for name, target in ALLOWED:
        source = (HERE / name).read_text(encoding="utf-8")
        assert f'"{target}"' in source, (
            f"{name} no longer references {target}; remove the exception")
