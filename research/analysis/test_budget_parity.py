"""Pinning tests for the budget-parity record (PREREG_BUDGET_PARITY).

Both traces are in and scored. These tests pin what the campaign decided, so
that no later edit can move a verdict without a test going red — the failure
mode the prereg's "runs once, nothing altered after the first result is
seen" rule exists to prevent. Azure was scored first, while BurstGPT was
still running; the two carry separate Holm families and are pinned
separately, which is what makes that ordering harmless.

They read the committed campaign CSVs only. Nothing is re-simulated, so they
are cheap enough to run on every commit.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analysis_budget_parity as bp  # noqa: E402

WINDOWS = {"azure": 72, "burstgpt": 96}


def _runs(trace: str) -> pd.DataFrame:
    return pd.read_csv(bp.TRACES[trace]["runs"])


class CampaignShape(unittest.TestCase):
    def test_all_ten_arms_ran_every_window(self):
        """A short arm would bias every paired test toward whichever windows
        survived."""
        for trace, n in WINDOWS.items():
            counts = _runs(trace).groupby("system").size()
            self.assertEqual(sorted(counts.index),
                             sorted(bp.WP1_ARMS + bp.NEW_ARMS), trace)
            self.assertEqual(set(counts.values), {n}, trace)

    def test_wp1_arms_replicate_exactly(self):
        """The prereg's halt condition: both mechanisms default OFF, so the
        seven inherited arms must reproduce `trace_parity_*_runs.csv`
        bit-for-bit (R4). Not 'within tolerance' — exactly."""
        for trace, n in WINDOWS.items():
            rep = bp.replication_check(_runs(trace),
                                       pd.read_csv(bp.TRACES[trace]["wp1"]))
            self.assertEqual(rep["_rows"], float(n * len(bp.WP1_ARMS)), trace)
            for col in bp.REPLICATION_COLS:
                self.assertEqual(rep[col], 0.0, f"{trace}: {col} diverged")


class Verdicts(unittest.TestCase):
    """The eight scored hypotheses, pinned in the direction they landed."""

    def test_bp_h1_fails_on_every_capped_comparator(self):
        """jcac is nominally cheaper than the capped comparators on both
        traces — by -50% on BurstGPT — and on neither does it survive the
        Wilcoxon gate. The per-window differences are bimodal, so the mean
        and the rank test disagree. Per the pre-committed response the
        like-for-like cost claim is withdrawn, not restated."""
        expected = {("azure", "hpa_budget"): -0.033,
                    ("azure", "keda_budget"): -0.013,
                    ("burstgpt", "hpa_budget"): -0.502,
                    ("burstgpt", "keda_budget"): -0.498}
        for (trace, base), rel in expected.items():
            r = bp.paired(_runs(trace), "jcac", base, "total_cost_usd")
            self.assertAlmostEqual(r["rel"], rel, places=3, msg=f"{trace}/{base}")
            self.assertGreater(r["p"], 0.05, f"{trace}/{base}")

    def test_bp_h2_passes_on_every_fair_comparator(self):
        """With the budget lifted, jcac's overshoot is non-inferior at the
        inherited 0.05 margin on both traces — the artefact test the severity
        FAIL turns on."""
        expected = {("azure", "hpa_fair"): 0.0114,
                    ("azure", "keda_fair"): 0.0114,
                    ("burstgpt", "hpa_fair"): 0.1037,
                    ("burstgpt", "keda_fair"): 0.1036}
        for (trace, base), diff in expected.items():
            r = bp.paired(_runs(trace), "jcac_nobudget", base,
                          "mean_excess", bp.NI_MARGIN)
            self.assertAlmostEqual(r["mean_diff"], diff, places=4, msg=f"{trace}/{base}")
            self.assertLess(r["p"], 0.0125, f"{trace}/{base}")  # tightest Holm threshold

    def test_holm_family_is_four_per_trace(self):
        """A fifth hypothesis smuggled into a family would loosen every
        threshold in it, and the two traces must never share one."""
        for trace in WINDOWS:
            df = _runs(trace)
            h = {
                "BP-H1a": bp.paired(df, "jcac", "hpa_budget", "total_cost_usd"),
                "BP-H1b": bp.paired(df, "jcac", "keda_budget", "total_cost_usd"),
                "BP-H2a": bp.paired(df, "jcac_nobudget", "hpa_fair", "mean_excess", bp.NI_MARGIN),
                "BP-H2b": bp.paired(df, "jcac_nobudget", "keda_fair", "mean_excess", bp.NI_MARGIN),
            }
            holm = bp.holm_bonferroni({k: v["p"] for k, v in h.items()})
            self.assertEqual(len(holm), 4, trace)
            self.assertEqual([holm[k]["reject"] for k in
                              ("BP-H1a", "BP-H1b", "BP-H2a", "BP-H2b")],
                             [False, False, True, True], trace)


class NonInferiorityShape(unittest.TestCase):
    def test_burstgpt_pass_is_a_typical_window_claim_only(self):
        """The disclosure that keeps the BP-H2 PASS honest. On BurstGPT the
        rank test passes on a median difference of exactly zero while the
        mean exceeds the margin it was tested against, because a fifth of
        windows breach it and one breaches it 59x over. If this ever stops
        being true the record's caveat must be re-derived, not deleted."""
        s = bp.ni_shape(_runs("burstgpt"), "hpa_fair")
        self.assertGreater(s["mean"], bp.NI_MARGIN)
        self.assertAlmostEqual(s["median"], 0.0, places=6)
        self.assertGreater(s["over"], 0.20)
        self.assertGreater(s["worst"], 2.9)

    def test_azure_pass_needs_no_such_caveat(self):
        """On Azure the mean, the median and the rank test agree, which is
        why only one trace carries the caveat."""
        s = bp.ni_shape(_runs("azure"), "hpa_fair")
        self.assertLess(s["mean"], bp.NI_MARGIN)
        self.assertLess(s["over"], 0.10)


class Amendment1Feasibility(unittest.TestCase):
    def test_the_cap_never_reaches_tier_spend(self):
        """The feasibility claim, made executable: applying the controller's
        own budget rule to a replica-only arm leaves tier spend *identical*
        and moves only infra. If this ever fails, the claim that no
        replica-only arm can meet the budget must be re-derived."""
        for trace in WINDOWS:
            df = _runs(trace)
            for fair, capped in (("hpa_fair", "hpa_budget"),
                                 ("keda_fair", "keda_budget")):
                f_infra, f_tier = bp.spend_split(df, fair)
                c_infra, c_tier = bp.spend_split(df, capped)
                self.assertEqual(round(f_tier, 10), round(c_tier, 10),
                                 f"{trace}/{capped}")
                self.assertLess(c_infra, f_infra, f"{trace}/{capped}")

    def test_shedding_is_exclusive_to_the_tier_holding_arms(self):
        """Only arms holding the tier knob ever shed to `tier=none`; every
        replica-only arm sheds in 0% of steps, capped or not. That is why
        the cap cannot bind them."""
        for trace in WINDOWS:
            shed = _runs(trace).groupby("system").tier_none_step_share.mean()
            for arm in ("hpa", "keda", "firm", "hpa_fair", "keda_fair",
                        "hpa_budget", "keda_budget"):
                self.assertEqual(shed[arm], 0.0, f"{trace}: {arm} shed")
            self.assertGreater(shed["jcac"], shed["jcac_nobudget"], trace)
            self.assertGreater(shed["jcac_nobudget"], 0.0, trace)

    def test_capping_a_replica_only_arm_buys_little_and_costs_slo(self):
        """The cap saves a few percent of total spend and multiplies
        overshoot on both traces — the measured form of 'the knob does not
        reach the cost that dominates'."""
        for trace in WINDOWS:
            df = _runs(trace)
            for fair, capped in (("hpa_fair", "hpa_budget"),
                                 ("keda_fair", "keda_budget")):
                cost = df[df.system == capped].total_cost_usd.mean()
                base = df[df.system == fair].total_cost_usd.mean()
                self.assertGreater(cost / base, 0.93, f"{trace}/{capped}")
                self.assertGreater(df[df.system == capped].mean_excess.mean(),
                                   df[df.system == fair].mean_excess.mean(),
                                   f"{trace}/{capped}")


if __name__ == "__main__":
    unittest.main()
