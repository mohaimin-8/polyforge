"""The WP17 planners' decision over the (replicas, CPU) lattice, standard library
only, so the twin (twin.py) and the planner pod in the cluster
(testbed/hvpa/planner) run the same code (programme 2).

Every lattice point is scored by the plant for the next interval at the
predicted rate: J = cost + KAPPA x late requests. The box: replicas
r - w ... r + w within [RMIN, RMAX], CPU one level down, the same or up
(plant.cpu_levels), each point fitting the node's CPU. Joint: the best point;
per-knob: the best replica count at the current CPU and the best CPU at the
current replica count, combined. Ties: stay, then the cheaper allocation.
Logged per decision: the interaction index (oscillation over the box of the
remainder J(r', c') - J(r', c) - J(r, c') + J(r, c), research/theory/
INDEX_THEORY.md) and the one-step loss of the per-knob choice.
"""

from __future__ import annotations

import math

PLAN_S = 30.0
NODE_CPU = 6.0
RMIN, RMAX = 1, 8
KAPPA = 1e-4           # USD per late request
EPS = 1e-9


def lattice_j(plant, rate: float, r: int, c: float, seconds: float = PLAN_S) -> float:
    out = plant.step(rate, [c] * r, seconds)
    return out["cost"] + KAPPA * out["late"]


def fits(r: int, c: float, node_cpu: float = NODE_CPU) -> bool:
    return r * c <= node_cpu + EPS


def box(plant, r: int, c: float, width: int) -> tuple[list[int], list[float]]:
    levels = list(plant.cpu_levels)
    i = min(range(len(levels)), key=lambda k: abs(levels[k] - c))
    rs = [x for x in range(r - width, r + width + 1) if RMIN <= x <= RMAX]
    return rs, levels[max(0, i - 1):i + 2]


def decide(plant, rate: float, r: int, c: float, width: int, joint: bool) -> tuple[tuple, dict]:
    """(next (replicas, CPU), log record) of the joint or the per-knob planner."""
    rs, cs = box(plant, r, c, width)
    J = {(rr, cc): lattice_j(plant, rate, rr, cc) for rr in rs for cc in cs if fits(rr, cc)}
    if (r, c) not in J:
        J[(r, c)] = lattice_j(plant, rate, r, c)   # the current state is scored even if a resize left it over
    key = lambda rc: (J[rc], rc != (r, c), rc[0] * rc[1])  # noqa: E731
    best = min(J, key=key)
    best_r = min((k for k in J if k[1] == c), key=key)[0]
    best_c = min((k for k in J if k[0] == r), key=key)[1]
    perknob = (best_r, best_c)
    rem = [J[(rr, cc)] - J[(rr, c)] - J[(r, cc)] + J[(r, c)] for rr in rs for cc in cs
           if (rr, cc) in J and (rr, c) in J and (r, cc) in J]
    info = {"index": max(rem) - min(rem) if rem else math.nan, "perknob_fits": perknob in J,
            "loss": (J[perknob] - J[best]) if perknob in J else math.nan}
    return (best if joint else perknob), info
