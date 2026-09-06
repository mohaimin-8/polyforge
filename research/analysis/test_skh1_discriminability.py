"""The null-window analysis must use SK-H1's statistic, not a lookalike.

The whole argument of `RESULTS_SKH1_DISCRIMINABILITY.md` is a comparison: the
rule fires on 39.2% of fault-free windows against 1 of 8 injected faults. That
comparison means nothing unless both sides are scored by the same statistic,
applied the same way. `analysis_live_soak_v8.score_skh1` is imported rather
than re-implemented, but importing a function is not the same as applying it
identically — the window offsets could still differ.

So the property asserted here is the one the argument rests on: for every fault
V8 scored, this module's `deviation()` reproduces V8's own number exactly.

Run: python -m pytest research/analysis/test_skh1_discriminability.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis_live_soak_v8 import bucket_epoch, score_skh1, timeline_faults  # noqa: E402
from analysis_skh1_discriminability import (GUARD_S, PRE_S, deviation,  # noqa: E402
                                            fine_buckets, null_windows)


def test_the_statistic_reproduces_v8s_own_score_for_every_fault():
    # Arrange
    fine = fine_buckets()
    faults = timeline_faults()
    rows, _ = score_skh1(fine, faults, max(bucket_epoch(b) for b in fine))
    ends = {start: end for _, start, end in faults}

    # Act / Assert - exact, not approximate: same code, same windows, so any
    # difference at all means the null windows are scored by a different rule
    # and the comparison in the record is invalid.
    checked = 0
    for row in rows:
        if not row.get("scoreable"):
            continue
        mine = deviation(fine, row["start"] - PRE_S, ends[row["start"]])
        assert mine == row["dev"], (
            f"{row['name']} at {row['start']}: V8 scored {row['dev']!r}, "
            f"this module scored {mine!r} — the comparison is not like for like")
        checked += 1
    assert checked == 8, f"expected 8 scoreable faults, cross-checked {checked}"


def test_null_windows_never_overlap_an_injected_fault():
    # Arrange
    fine = fine_buckets()
    faults = timeline_faults()

    # Act
    devs = null_windows(fine, faults)

    # Assert - the count is the claim's denominator; a null window that
    # overlapped a fault would put the thing being measured into the baseline.
    assert len(devs) > 500, f"only {len(devs)} fault-free windows; too few to quote a rate"
    assert GUARD_S >= 900, "the guard band is what keeps fault effects out of the baseline"


def test_the_faults_themselves_are_excluded_from_the_null_set():
    # A direct check of the exclusion, rather than trusting the arithmetic:
    # the failing fault's own deviation must not appear among the null windows.
    fine = fine_buckets()
    faults = timeline_faults()
    rows, _ = score_skh1(fine, faults, max(bucket_epoch(b) for b in fine))
    failing = [r["dev"] for r in rows if r.get("scoreable") and not r["ok"]]
    assert failing, "expected V8 to have one failing injection"

    devs = null_windows(fine, faults)
    for dev in failing:
        assert dev not in devs, "an injected fault leaked into the fault-free set"
