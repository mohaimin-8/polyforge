"""Numbers the roadmap quotes must still be the numbers the records report.

The records are gated byte-for-byte by scripts/reproduce.py. The roadmap is
prose that COPIES from them, and nothing checked the copy. Session 43 found
`0.3166` quoted in three places for a node share that RESULTS_MULTINODE.md
reports as `0.3165` -- a transcription slip in the document that drives
execution, invisible to every gate in the repo because no gate reads prose.

The direction of the check matters: if the roadmap quotes a figure, the record
must contain it. It does NOT require the roadmap to keep quoting anything, so
dropping a claim is free and changing a measured value is not.

Run: python -m pytest scripts/test_roadmap_claims.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ROADMAP = REPO_ROOT / "docs" / "FINAL_ROADMAP.md"
ANALYSIS = REPO_ROOT / "research" / "analysis"

# (what it is, the exact string, the record that must contain it)
CLAIMS = [
    ("S3 eight-tenant floor", "0.126189", "RESULTS_SEPARATION_MT_V3.md"),
    ("M2 share of overshoot from the budget cap", "79.5", "RESULTS_WINDOW_CHARACTER.md"),
    ("W7 node share, worker 1", "0.3165", "RESULTS_MULTINODE.md"),
    ("W7 node share, busiest", "0.3815", "RESULTS_MULTINODE.md"),
    ("W7 node share, worker 3", "0.3019", "RESULTS_MULTINODE.md"),
    ("W7 placement hypothesis", "MN-H1", "RESULTS_MULTINODE.md"),
    ("attempt 10 request count", "25,806,353", "RESULTS_LIVE_SOAK_V8.md"),
    ("attempt 10 recovery hypothesis", "SK-H1", "RESULTS_LIVE_SOAK_V8.md"),
    ("L5 trace-driven hypotheses", "TL-H1", "RESULTS_TRACE_LIVE.md"),
    ("L5 delivery gate", "TL-H3", "RESULTS_TRACE_LIVE.md"),
    ("cost claim at budget parity", "BP-H1", "RESULTS_BUDGET_PARITY.md"),
    ("learned-controller comparison", "LR-H1", "RESULTS_LEARNED.md"),
    ("C1 oracle same-model precision", "0.4898", "RESULTS_CACHE_CEILING.md"),
]


@pytest.mark.parametrize("what,value,record", CLAIMS,
                         ids=[c[0].replace(" ", "_") for c in CLAIMS])
def test_a_quoted_number_matches_its_record(what, value, record):
    roadmap = ROADMAP.read_text(encoding="utf-8")
    if value not in roadmap:
        pytest.skip(f"the roadmap no longer quotes {value} for {what}")
    path = ANALYSIS / record
    assert path.exists(), f"{record} is missing; {what} has no source"
    assert value in path.read_text(encoding="utf-8"), (
        f"the roadmap quotes {value} for {what}, but {record} does not "
        f"contain it -- the prose and the measurement have diverged")


def test_the_claim_table_is_not_silently_empty():
    assert len(CLAIMS) >= 13, "claims were removed; this check would pass on nothing"
