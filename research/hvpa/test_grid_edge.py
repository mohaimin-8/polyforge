"""The tuning-grid edge check's rule."""

import grid_edge


def test_the_edge_binds_only_when_a_lower_target_has_a_lower_j():
    assert not grid_edge.binds({0.05: 2.5, 0.075: 2.5, 0.1: 2.5}, 0.1)      # flat: the HPA is pinned
    assert grid_edge.binds({0.05: 3.0, 0.075: 3.0, 0.1: 3.03}, 0.1)
    assert not grid_edge.binds({0.05: 3.1, 0.1: 3.0}, 0.1)
    assert not grid_edge.binds({0.1: 3.0}, 0.1)                              # nothing below the edge


def test_each_hpa_arm_names_the_live_arm_it_stands_for():
    assert grid_edge.LIVE_ARM == {"cpu": "A1", "rate": "A2"}
