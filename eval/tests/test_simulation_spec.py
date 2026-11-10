"""docs/SIMULATION_SPEC.md must match the code that runs the campaigns.

The generator quotes live constants, arm definitions and tuned parameters.
Nothing forced a regeneration, so the document went stale once (2026-11-10:
the four GPU-ladder arms tuned for LEVEL2 were missing). This test fails
whenever the code changes and the document does not.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("simulation_spec", REPO / "scripts" / "simulation_spec.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_spec_matches_the_code(generator):
    generator.model.set_model_form()  # published plant, whatever an earlier test left behind
    committed = generator.OUT.read_text(encoding="utf-8")
    assert generator.render() == committed, "stale: run python scripts/simulation_spec.py"


def test_check_mode_writes_nothing_and_reports_staleness(generator, tmp_path, monkeypatch):
    generator.model.set_model_form()
    stale = tmp_path / "SIMULATION_SPEC.md"
    stale.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(generator, "OUT", stale)
    assert generator.main(["--check"]) == 1
    assert stale.read_text(encoding="utf-8") == "old\n"
    assert generator.main([]) == 0
    assert generator.main(["--check"]) == 0
