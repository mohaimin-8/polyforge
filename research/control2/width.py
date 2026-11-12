"""The joint planner with a settable actuation width (programme 2, WP4).

`WidthController` is `controller.JCACController` whose move lattice allows
up to `replica_radius` replicas and `cache_radius` cache levels per interval
in either direction (any tier, as before). Width 2 with cache radius 1 is the
published lattice exactly; `test_width.py` checks that runs are identical.

A subclass rather than a new flag on JCACController (plan, WP0): the
published controller keeps the code every registered campaign ran.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SIM = Path(__file__).resolve().parents[1] / "jcac_sim"
if str(_SIM) not in sys.path:
    sys.path.insert(0, str(_SIM))

import model  # noqa: E402
from controller import JCACController  # noqa: E402

# Cache radius used with each replica width in the dose-response design:
# one level for small steps, two at 4, any level from 8 (as WIDTH's wide lattice).
CACHE_RADIUS_FOR_WIDTH = {1: 1, 2: 1, 4: 2, 8: len(model.CACHE_LEVELS_MB) - 1,
                          16: len(model.CACHE_LEVELS_MB) - 1}


class WidthController(JCACController):
    """JCACController with a move lattice of the given radii."""

    def __init__(self, *args, replica_radius: int | None = None,
                 cache_radius: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if replica_radius is not None:
            if replica_radius < 1:
                raise ValueError("replica_radius must be at least 1")
            self._delta_replicas = tuple(range(-replica_radius, replica_radius + 1))
        if cache_radius is not None and cache_radius < 1:
            raise ValueError("cache_radius must be at least 1")
        self._cache_radius = cache_radius

    def _cache_candidates(self, cache_mb: int) -> tuple[int, ...]:
        if self._cache_radius is None:
            return super()._cache_candidates(cache_mb)
        levels = model.CACHE_LEVELS_MB
        if cache_mb not in levels:
            return (levels[0],)
        i = levels.index(cache_mb)
        return tuple(levels[max(0, i - self._cache_radius): i + self._cache_radius + 1])
