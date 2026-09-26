"""Shared test isolation for the harness suite.

The simulator's economy and structural model form are module state
(`model.set_economy`, `model.set_model_form`). `sim_backend.execute` sets both
at the start of every run, but a test that runs a campaign cell under an
altered form and then ends leaves that form behind for whichever test runs
next in the process -- which is how a model-form test once silently changed
the published-solver golden hash in `test_solver_convergence.py`. Every test
therefore ends with the published economy and forms restored.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from harness import workloads  # noqa: E402,F401  (puts research/jcac_sim on sys.path)
import model  # noqa: E402


@pytest.fixture(autouse=True)
def published_model_state():
    yield
    model.set_economy()
    model.set_model_form()
