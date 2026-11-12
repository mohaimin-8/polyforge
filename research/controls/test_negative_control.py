"""WP22 negative control: an exactly additive objective has a zero index and
makes the joint and the per-knob planner choose identically; the only
separations are the ones outside Theorem 1, each explained."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import controls_common as cc  # noqa: E402  (sets the import paths)
import negative_control as nc  # noqa: E402
import pytest  # noqa: E402

STEPS = 20
ZERO_CONTROLS = [c.name for c in nc.controls(1) if c.expect_zero]


def test_separable_objectives_have_zero_index_and_identical_choices():
    u = nc.unit_control(n_boxes=3000)
    assert u["worst_index"] <= 1e-12
    assert u["worst_loss"] <= 1e-12
    assert u["disagreements"] == 0


def test_the_two_additive_exceptions_lie_outside_theorem1():
    exc = nc.unit_exceptions()
    budget = exc["budget"]  # constraint coupling: R = 0 where defined, x^D infeasible
    assert budget.oscillation == 0.0
    assert budget.per_knob == (1, 1) and budget.phi_per_knob is None
    assert budget.joint == (1, 0) and not budget.theorem1_applies
    anchor = exc["anchor"]  # infeasible anchor: no remainder anywhere, knobs hold
    assert anchor.phi_anchor is None and anchor.oscillation is None
    assert anchor.per_knob != anchor.joint and not anchor.theorem1_applies


def test_committed_jointness_crud_cells_are_bit_identical():
    df = nc.committed_check()
    crud = df[df.crud_only]
    assert int(crud.pairs.sum()) == 120 and (crud.identical == crud.pairs).all()
    ai = df[~df.crud_only]
    assert (ai.identical < ai.pairs).all()  # the same comparison can fail, and does where AI couples


@pytest.mark.parametrize("name", ZERO_CONTROLS)
def test_simulator_control_has_zero_index_and_identical_decisions(name):
    ctl = nc.control_by_name(name, 1)
    workload, mix, size = ctl.cells[0]
    row = nc.run_cell((name, workload, mix, size, STEPS, 1))
    assert row["decisions"] == 8 * STEPS
    assert row["index_defined"] == row["decisions"]
    assert row["index_nonzero"] == 0 and row["index_max_rel"] <= nc.TOL_REL, row["first_offender"]
    assert row["box_disagreements"] == 0, row["first_offender"]
    assert row["planner_disagreement_share"] == 0.0
    assert row["identical"] and row["max_metric_diff"] == 0.0
    if name != "crud_only":  # the published planner rarely moves; the others must exercise their carrier
        assert row[f"moved_{ctl.carrier}"] > 0.0


def test_sensitivity_row_sees_the_couplings_of_published_mode_a():
    ctl = nc.control_by_name("mode_a_published", 1)
    workload, mix, size = ctl.cells[0]
    row = nc.run_cell(("mode_a_published", workload, mix, size, 15, 1))
    assert row["index_mean"] > 0.01  # the same pipeline reports a positive index when couplings remain


def test_binding_caps_disagreements_are_all_explained():
    """CRUD-only demand (additive) under a binding cluster replica cap and no
    switching penalty. Every disagreement between the decoupled and the
    joint best response from identical inputs is either an infeasible
    anchor (outside Theorem 1) or the decoupled arm's convergence gate
    keeping an out-of-lattice incumbent -- the asymmetry reproduced here
    (controller.py:1280-1284 against 1104-1121). With the published
    switching penalty there is none."""
    probe = nc.cap_binding_probe(steps=3, switch_penalty=0.0)
    assert "UNEXPLAINED" not in probe["by_reason"]
    assert probe["by_reason"].get("anchor infeasible", 0) >= 1
    kept = [d for d in probe["disagreements"] if d["why"].startswith("decoupled gate")]
    assert kept
    for d in kept:
        assert d["decoupled"] == d["incumbent"] and d["incumbent"].cache_mb == 0
        assert d["base"].cache_mb == 128  # two cache levels from the anchor: outside the one-level clamp
    # the diagnosis: gate only on lattice members (as the joint search does) and that class vanishes
    symmetric = nc.cap_binding_probe(steps=3, switch_penalty=0.0, symmetric_gate=True)
    assert symmetric["by_reason"] == {"anchor infeasible": probe["by_reason"]["anchor infeasible"]}
    assert nc.cap_binding_probe(steps=3, switch_penalty=None)["disagreements"] == []


def test_arms_are_removed_from_the_registry_after_a_run():
    from harness.systems import SYSTEMS

    nc.run_cell(("crud_only", "crud_steady", "uniform", "small", 3, 1))
    assert cc.JOINT not in SYSTEMS and cc.DECOUPLED not in SYSTEMS
