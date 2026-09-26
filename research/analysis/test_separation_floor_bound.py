"""The published separation floor is exact under its construction.

Audit 2026-09-26 read `guarantee._violation_table`'s worst-case fold over
positions sharing a need level as an over-estimate of the floor, and offered
it as the explanation for S3's "keda 67x below the floor"
(RESULTS_SEPARATION_MT_V3.md). The fold IS an upper bound in general
(`bound="min"` is the valid lower bound). But on the published cell every
need level carries a single demand shape, so the two bounds coincide and the
published series (0.126189 at eight tenants) is exact for its model. The
S3 gap therefore does not come from this fold. These tests pin that, so a
change of orbit that makes the bounds diverge is noticed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _walk():
    spec = importlib.util.spec_from_file_location("walk", HERE / "separation_mt_v3_walk.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_each_need_level_of_the_published_cell_has_one_demand_shape():
    walk = _walk()
    c = walk.cell()
    needs = walk.guarantee.orbit_replica_needs(c["config"], c["orbit"])
    for level in set(needs):
        shapes = {str(d.rps) for d, n in zip(c["orbit"], needs) if n == level}
        assert len(shapes) == 1, (level, shapes)


def test_the_two_bounds_coincide_on_the_published_cell():
    walk = _walk()
    c = walk.cell()
    cfg, orbit, cap = c["config"], c["orbit"], c["cap"]
    g = walk.guarantee
    needs = g.orbit_replica_needs(cfg, orbit)
    assert (g._violation_table(cfg, orbit, needs, cfg.replica_max)
            == g._violation_table(cfg, orbit, needs, cfg.replica_max, bound="min"))
    hi = g.coupled_floor_incremental_batched(cfg, orbit, 5, cap)["coupled_violation"]
    lo = g.coupled_floor_incremental_batched(cfg, orbit, 5, cap, bound="min")["coupled_violation"]
    assert hi == lo > 0.0  # n=5 is the first size where the cap binds
