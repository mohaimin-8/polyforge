"""The dose-response report counts every logged decision, not only the ones Theorem 1 applies to."""

from pathlib import Path

import pandas as pd

import dose_response_pilot as wp4

HERE = Path(__file__).resolve().parent


def _theorem_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("Theorem 1"))


def test_the_report_states_how_many_logged_decisions_the_theorem_covers():
    """The pilot logged 480,000 decisions and the theorem applies to 456,876 of them; a line that
    gave only the second as 'over all logged decisions' was read as 'held on all 456,876 logged'."""
    runs = pd.read_csv(HERE / "data" / "dose_response_runs.csv.gz", float_precision="round_trip")
    line = _theorem_line(wp4.report(runs))
    assert f"all {int(runs.decisions.sum()):,} logged decisions" in line
    assert f"to {int(runs.theorem_applies.sum()):,}, {int(runs.theorem_violations.sum())} violations" in line
    assert runs.theorem_applies.sum() < runs.decisions.sum()
    assert line == _theorem_line((HERE / "DOSE_RESPONSE_PILOT.md").read_text(encoding="utf-8"))
