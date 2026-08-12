"""Pinning tests for the budget-parity record (PREREG_BUDGET_PARITY).

The Azure half of the campaign is complete and scored; BurstGPT is still
running. These tests pin what Azure decided, so that regenerating the
record when BurstGPT lands cannot silently move an already-scored verdict —
the failure mode the prereg's "runs once, nothing altered after the first
result is seen" rule exists to prevent.

They read the committed campaign CSV only. Nothing is re-simulated, so they
are cheap enough to run on every commit.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analysis_budget_parity as bp  # noqa: E402


def _azure() -> pd.DataFrame:
    return pd.read_csv(bp.TRACES["azure"]["runs"])


class AzureCampaignShape(unittest.TestCase):
    def test_all_ten_arms_ran_every_window(self):
        """72 windows x 10 arms. A short arm would bias every paired test
        toward whichever windows survived."""
        counts = _azure().groupby("system").size()
        self.assertEqual(sorted(counts.index), sorted(bp.WP1_ARMS + bp.NEW_ARMS))
        self.assertEqual(set(counts.values), {72})

    def test_wp1_arms_replicate_exactly(self):
        """The prereg's halt condition: both mechanisms default OFF, so the
        seven inherited arms must reproduce `trace_parity_azure_runs.csv`
        bit-for-bit (R4). Not 'within tolerance' — exactly."""
        rep = bp.replication_check(_azure(), pd.read_csv(bp.TRACES["azure"]["wp1"]))
        self.assertEqual(rep["_rows"], 504.0)
        for col in bp.REPLICATION_COLS:
            self.assertEqual(rep[col], 0.0, f"{col} diverged")


class AzureVerdicts(unittest.TestCase):
    """The four scored hypotheses, pinned in the direction they landed."""

    def test_bp_h1_fails_on_both_capped_comparators(self):
        """jcac is nominally cheaper than the capped comparators (-3.3% and
        -1.3%) but neither survives the Wilcoxon gate, so the like-for-like
        cost claim is withdrawn on this trace, exactly as pre-committed."""
        df = _azure()
        for base, rel in (("hpa_budget", -0.033), ("keda_budget", -0.013)):
            r = bp.paired(df, "jcac", base, "total_cost_usd")
            self.assertAlmostEqual(r["rel"], rel, places=3)
            self.assertGreater(r["p"], 0.05)

    def test_bp_h2_passes_on_both_fair_comparators(self):
        """With the budget lifted, jcac's overshoot is non-inferior to the
        fair baselines at the inherited 0.05 margin — the artefact test the
        severity FAIL turns on."""
        df = _azure()
        for base in ("hpa_fair", "keda_fair"):
            r = bp.paired(df, "jcac_nobudget", base, "mean_excess", bp.NI_MARGIN)
            self.assertAlmostEqual(r["mean_diff"], 0.0114, places=4)
            self.assertLess(r["p"], 0.0125)   # tightest Holm threshold of 4

    def test_holm_family_is_four_per_trace(self):
        """A fifth hypothesis smuggled into the family would loosen every
        threshold in it."""
        df = _azure()
        h = {
            "BP-H1a": bp.paired(df, "jcac", "hpa_budget", "total_cost_usd"),
            "BP-H1b": bp.paired(df, "jcac", "keda_budget", "total_cost_usd"),
            "BP-H2a": bp.paired(df, "jcac_nobudget", "hpa_fair", "mean_excess", bp.NI_MARGIN),
            "BP-H2b": bp.paired(df, "jcac_nobudget", "keda_fair", "mean_excess", bp.NI_MARGIN),
        }
        holm = bp.holm_bonferroni({k: v["p"] for k, v in h.items()})
        self.assertEqual(len(holm), 4)
        self.assertEqual([holm[k]["reject"] for k in
                          ("BP-H1a", "BP-H1b", "BP-H2a", "BP-H2b")],
                         [False, False, True, True])


class Amendment1Feasibility(unittest.TestCase):
    def test_the_cap_never_reaches_tier_spend(self):
        """The feasibility claim, made executable: applying the controller's
        own budget rule to a replica-only arm leaves tier spend *identical*
        and moves only infra. If this ever fails, the claim that no
        replica-only arm can meet the budget must be re-derived."""
        df = _azure()
        for fair, capped in (("hpa_fair", "hpa_budget"),
                             ("keda_fair", "keda_budget")):
            f_infra, f_tier = bp.spend_split(df, fair)
            c_infra, c_tier = bp.spend_split(df, capped)
            self.assertEqual(round(f_tier, 10), round(c_tier, 10))
            self.assertLess(c_infra, f_infra)

    def test_shedding_is_exclusive_to_the_budget_constrained_arms(self):
        """Only the arms holding the tier knob ever shed to `tier=none`;
        every replica-only arm sheds in 0% of steps, capped or not. That is
        why the cap cannot bind them."""
        shed = _azure().groupby("system").tier_none_step_share.mean()
        for arm in ("hpa", "keda", "firm", "hpa_fair", "keda_fair",
                    "hpa_budget", "keda_budget"):
            self.assertEqual(shed[arm], 0.0, f"{arm} shed unexpectedly")
        self.assertGreater(shed["jcac"], shed["jcac_nobudget"])
        self.assertGreater(shed["jcac_nobudget"], 0.0)


if __name__ == "__main__":
    unittest.main()
