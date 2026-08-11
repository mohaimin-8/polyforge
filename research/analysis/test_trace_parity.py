"""Wiring tests for the trace-replay parity substrate (PREREG_TRACE_PARITY).

`trace_matrix.run_one` is the shared runner behind `RESULTS_TRACE.md`,
`RESULTS_TRACE2.md` and `RESULTS_TRACE_AZURE.md`. Session 37 threaded two
`SystemSpec` attributes it had been dropping. These tests hold both halves
of that change: the fair comparators now mean something on this substrate,
and every published arm is provably unaffected (R4).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trace_matrix as tm  # noqa: E402  (inserts the jcac_sim / eval paths)
import trace_parity  # noqa: E402
from harness.systems import SYSTEMS  # noqa: E402
from model import Demand  # noqa: E402


def _buckets(steps: int = 24) -> list[dict]:
    """AI-heavy demand across the substrate's eight tenants: the tier spend
    the eviction charge scales has to be nonzero for any of this to bite."""
    return [
        {f"t{i:02d}": Demand(rps={"chat": 2.0 + i, "crud_read": 10.0})
         for i in range(tm.TENANTS)}
        for _ in range(steps)
    ]


class TraceParityWiring(unittest.TestCase):
    def test_published_arms_carry_neither_new_attribute(self):
        """The R4 argument, made executable. Threading `static_cache_mb` and
        `evict_overhead_us` can only change an arm that sets one; no published
        arm does, so every committed trace record replays unchanged."""
        for arm in trace_parity.SYSTEMS_UNDER_TEST:
            if arm in ("hpa_fair", "keda_fair"):
                continue
            spec = SYSTEMS[arm]
            self.assertIsNone(spec.static_cache_mb, f"{arm} pins a cache")
            self.assertNotIn("evict_overhead_us", spec.params, f"{arm} charges eviction")

    def test_fair_arms_are_pre_sized_and_uncharged(self):
        """What the prereg's comparator is: 512 MB, no LRU charge, everything
        else identical to the published arm it replaces."""
        for fair, published in (("hpa_fair", "hpa"), ("keda_fair", "keda")):
            f, p = SYSTEMS[fair], SYSTEMS[published]
            self.assertEqual(f.static_cache_mb, 512)
            self.assertFalse(f.lru_eviction)
            self.assertTrue(p.lru_eviction)
            self.assertEqual(f.controller, p.controller)
            self.assertEqual(f.params, p.params)
            self.assertEqual(f.gamma, p.gamma)

    def test_static_cache_mb_reaches_the_engine(self):
        """Before this wiring, `run_one` ignored `static_cache_mb`, so
        `hpa_fair` would have silently run at the default 128 MB — an arm
        differing from `hpa` only by the charge, not the comparator the
        pre-registration scores against. A different realized hit rate is the
        observable proof the 512 MB pre-size took effect."""
        buckets = _buckets()
        hpa = tm.run_one(("hpa", 0, buckets))
        fair = tm.run_one(("hpa_fair", 0, buckets))
        self.assertNotAlmostEqual(
            hpa["cache_hit_rate"], fair["cache_hit_rate"], places=6
        )
        self.assertGreater(fair["cache_hit_rate"], hpa["cache_hit_rate"])
        # No LRU charge on top of a better hit rate: strictly cheaper.
        self.assertLess(fair["total_cost_usd"], hpa["total_cost_usd"])

    def test_run_one_reports_the_severity_and_band_columns(self):
        """The three metrics the engine computed and this substrate discarded.
        Without them the record cannot state unbounded severity (TP-H3) or
        recompute the eviction band exactly."""
        row = tm.run_one(("jcac", 0, _buckets()))
        for col in ("mean_excess", "tier_none_step_share", "total_tier_cost_usd"):
            self.assertIn(col, row)
        self.assertGreater(row["total_tier_cost_usd"], 0.0)
        self.assertGreaterEqual(row["mean_excess"], 0.0)


if __name__ == "__main__":
    unittest.main()
