"""Proposition 1 made executable (audit 2026-09-26, CRITICAL).

THEORY_V2's proof that two fixed Gauss-Seidel sweeps end at a block-coordinate
minimum does not hold: a tenant visited before the last mover best-responded to
that mover's OLD value. Measured on the published (unanchored) solver, 23 of
1,830 control cycles left a tenant with a strictly better unilateral move.

`converge_sweeps=True` (with `anchor_moves=True`) sweeps until a full sweep
changes no tenant, keeping the incumbent on ties, so the returned plan is a
best response for every tenant by construction. These tests pin: (1) the
published solver is bit-identical to before the flag existed; (2) the
convergent solver leaves no tenant an improving move on real harness cells;
(3) the published solver does leave some -- the defect, documented.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import workloads  # noqa: E402  (also puts research/jcac_sim on sys.path)
import simulate  # noqa: E402
from controller import JCACController  # noqa: E402

GOLDEN_CELLS = [("crud_bursty", "uniform", "small", 11), ("ai_cacheable", "whale", "medium", 12),
                ("agentic", "premium_heavy", "small", 13), ("joint_stress", "uniform", "medium", 14),
                ("tier_mixed", "besteffort_heavy", "small", 15), ("flash_ai", "uniform", "large", 16)]
# sha256 over (cost, violation, jain, hit rate) of jcac and jcac_anchored on
# GOLDEN_CELLS, 40 steps, recorded from the solver BEFORE converge_sweeps
# existed (2026-09-26, commit f5f66cd). Any change to the published path fails.
GOLDEN = "086e22e8ea434feceb9b2916b70d1a85547a3c816384d8a1bbf95e4963369919"

CHECK_CELLS = [(wl, mix, size) for wl in ("crud_bursty", "ai_cacheable", "agentic",
                                          "joint_stress", "tier_mixed")
               for mix in ("uniform", "whale", "premium_heavy")
               for size in ("small", "medium")]


def test_published_solver_is_bit_identical_to_before_the_flag():
    h = hashlib.sha256()
    for anchored in (False, True):
        for wl, mix, size, seed in GOLDEN_CELLS:
            tids, buckets, configs, limits = workloads.build(wl, mix, size, seed, 40)
            r = simulate.run("jcac", tids, buckets, configs=configs, limits=limits,
                             collect_rows=False, controller_params={"anchor_moves": anchored},
                             jitter_seed=seed)
            h.update(repr((r.total_cost_usd, r.mean_violation, r.mean_jain,
                           r.cache_hit_rate)).encode())
    assert h.hexdigest() == GOLDEN


def _count_improvable(params: dict, monkeypatch) -> tuple[int, int, int]:
    """Run every CHECK_CELL; after each plan() count tenants whose best
    unilateral move scores strictly below their plan. Returns (cycles,
    cycles with an improvable tenant, cycles that hit the sweep cap)."""
    stats = {"cycles": 0, "bad": 0, "capped": 0}
    orig = JCACController.plan

    def checked(self, states, demands, interference=None):
        plans = orig(self, states, demands, interference)
        stats["cycles"] += 1
        gaps = self.best_response_gaps()
        if any(g > 1e-12 for g in gaps.values()):
            stats["bad"] += 1
        if self.last_converged is False:
            stats["capped"] += 1
        return plans

    monkeypatch.setattr(JCACController, "plan", checked)
    for wl, mix, size in CHECK_CELLS:
        tids, buckets, configs, limits = workloads.build(wl, mix, size, 7, 60)
        simulate.run("jcac", tids, buckets, configs=configs, limits=limits,
                     collect_rows=False, controller_params=params, jitter_seed=7)
    return stats["cycles"], stats["bad"], stats["capped"]


def test_convergent_solver_leaves_no_tenant_an_improving_move(monkeypatch):
    cycles, bad, capped = _count_improvable(
        {"anchor_moves": True, "converge_sweeps": True}, monkeypatch)
    assert cycles > 1000
    assert (bad, capped) == (0, 0)


def test_published_solver_can_leave_an_improving_move(monkeypatch):
    # The defect Proposition 1's proof missed, measured on the arm behind the
    # 1,800-run headline. Pinned so nobody "fixes" the published path.
    cycles, bad, _ = _count_improvable({"anchor_moves": False}, monkeypatch)
    assert cycles > 1000 and bad > 0
