"""RESULTS_LIVE_SOAK_V5's prose must agree with its own tables.

Audit 2026-09-26: the generator reused attempt 6's hard-coded sentences, so
the record said "the margin is 0.003 ms" beside a 5.076 ms maximum, "failed on
one of three scoreable occurrences" beside a 4-of-4 FAIL table, "not memory
(zero restarts)" beside 13 restarts, and "the cause is now isolated" beside
"the cause returns to unknown". Verdicts were right; the words were not.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _record(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("soak5", HERE / "analysis_live_soak_v5.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build()


def test_prose_matches_the_computed_tables(monkeypatch, tmp_path):
    text = _record(monkeypatch, tmp_path)
    assert "0.003 ms" not in text
    assert "one of three" not in text
    assert "zero restarts" not in text
    assert "now isolated" not in text
    assert "eleven and a half million" not in text
    # The SK-H1 count sentence must equal the table's own FAIL rows.
    table_fails = len(re.findall(r"^\| (?:planner|cpu) #\d .*\*\*FAIL\*\* \|$", text, re.M))
    scoreable = len(re.findall(r"^\| (?:planner|cpu) #\d \| \d", text, re.M))
    assert f"failed on {table_fails} of {scoreable} scoreable occurrences" in text


def test_the_after_recovery_excursion_names_the_right_fault(monkeypatch, tmp_path):
    # Only faults whose recovery peak exceeds their during-fault peak may be
    # named as "the excursion arrived after the fault ended".
    text = _record(monkeypatch, tmp_path)
    rows = re.findall(r"^\| ((?:planner|cpu) #\d) \| [\d.]+ ms \| ([\d.]+) ms \| ([\d.]+) ms", text, re.M)
    after = [name for name, during, rec in rows if float(rec) > float(during)]
    m = re.search(r"arrived AFTER the fault ended on: ([^.]+)\.", text)
    assert m, "the sentence naming after-recovery excursions is missing"
    assert m.group(1) == ", ".join(f"`{n}`" for n in after)
