"""Interaction index: how much per-knob search can lose against joint search.

Programme 2, work package WP3. Definitions, theorems and proofs are in
INDEX_THEORY.md beside this file; this module is their executable form.

Setting. One decision over K knobs. Knob k takes a value in a finite list
V_k; the box is B = V_1 x ... x V_K. An objective phi maps a point of B to a
float, or to None when the point is infeasible. The anchor x0 is a point of
B (the state the decision starts from).

    main effect   f_k(v) = phi(x0 with knob k set to v) - phi(x0)
    remainder     R(x)   = phi(x) - phi(x0) - sum_k f_k(x_k)

R is zero on every axis through the anchor. Per-knob search picks, for each
knob separately, a minimiser of its main effect and combines the picks (x^D);
joint search minimises phi over the whole box (x^J). Theorem 1:

    0 <= phi(x^D) - phi(x^J) <= R(x^D) - R(x^J) <= max_B R - min_B R.

The right-hand side is the interaction index of the decision. Everything is
pure: no module state, no randomness except in `sample_range`, which takes
its generator as an argument.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from typing import Callable, Hashable, Sequence

Point = tuple
Objective = Callable[[Point], "float | None"]

# Tail share used by the quantile index and the sampling guarantee (T5).
TAIL = 0.05


@dataclass(frozen=True)
class RemainderTable:
    """The anchored decomposition of phi on a box."""

    anchor: Point
    phi_anchor: float | None
    axis: tuple  # axis[k][v] = phi(anchor with knob k = v), None if infeasible
    remainder: dict  # point -> R(point), for points where R is defined
    phi: dict  # point -> phi(point) or None, for every point of the box


@dataclass(frozen=True)
class BoxAnalysis:
    """Per-knob against joint search on one box, with the interaction index."""

    anchor: Point
    phi_anchor: float | None
    per_knob: Point  # x^D: each knob's main-effect minimiser, combined
    joint: Point | None  # x^J: a minimiser of phi over the box
    phi_per_knob: float | None
    phi_joint: float | None
    loss: float | None  # G = phi(x^D) - phi(x^J), when both are feasible
    bound: float | None  # R(x^D) - R(x^J), when both remainders are defined
    oscillation: float | None  # max R - min R over the box (the index)
    quantile_index: float | None  # upper minus lower TAIL-quantile of R
    n_points: int
    n_feasible: int
    n_remainder: int

    @property
    def disagree(self) -> bool:
        return self.joint is not None and self.per_knob != self.joint

    @property
    def theorem1_applies(self) -> bool:
        """Theorem 1 speaks about this decision: the anchor, x^D and x^J are
        feasible and both remainders are defined."""
        return self.loss is not None and self.bound is not None


def _validate(values: Sequence[Sequence[Hashable]], anchor: Point) -> list[tuple]:
    vals = [tuple(v) for v in values]
    if not vals:
        raise ValueError("the box needs at least one knob")
    if any(len(v) == 0 for v in vals):
        raise ValueError("every knob needs at least one value")
    if any(len(set(v)) != len(v) for v in vals):
        raise ValueError("knob values must be distinct")
    if len(anchor) != len(vals):
        raise ValueError(f"anchor has {len(anchor)} coordinates, the box {len(vals)} knobs")
    for k, (a, v) in enumerate(zip(anchor, vals)):
        if a not in v:
            raise ValueError(f"anchor value {a!r} is not a value of knob {k}")
    return vals


def remainder_table(values: Sequence[Sequence[Hashable]], phi: Objective,
                    anchor: Point) -> RemainderTable:
    """Evaluate phi on the whole box once and decompose it at `anchor`.

    R(x) is defined where phi(x), phi(anchor) and every axis value
    phi(anchor with knob k = x_k) are feasible.
    """
    vals = _validate(values, anchor)
    anchor = tuple(anchor)
    table = {x: phi(x) for x in itertools.product(*vals)}
    phi0 = table[anchor]
    axis = tuple(
        {v: table[anchor[:k] + (v,) + anchor[k + 1:]] for v in vals[k]}
        for k in range(len(vals))
    )
    remainder = {}
    if phi0 is not None:
        for x, s in table.items():
            if s is None:
                continue
            mains = [axis[k][x[k]] for k in range(len(vals))]
            if any(m is None for m in mains):
                continue
            remainder[x] = s - phi0 - sum(m - phi0 for m in mains)
    return RemainderTable(anchor=anchor, phi_anchor=phi0, axis=axis,
                          remainder=remainder, phi=table)


def per_knob_choice(values: Sequence[Sequence[Hashable]], table: RemainderTable) -> Point:
    """x^D: for each knob, the first value in list order with the smallest
    feasible axis objective (strictly smaller wins, so ties keep the earlier
    value); a knob with no feasible value on its axis holds the anchor."""
    choice = []
    for k, vals in enumerate(values):
        best_v, best = table.anchor[k], math.inf
        for v in vals:
            s = table.axis[k][v]
            if s is not None and s < best:
                best_v, best = v, s
        choice.append(best_v)
    return tuple(choice)


def joint_choice(values: Sequence[Sequence[Hashable]], table: RemainderTable) -> Point | None:
    """x^J: the first point in product order with the smallest feasible phi."""
    best_x, best = None, math.inf
    for x in itertools.product(*values):
        s = table.phi[x]
        if s is not None and s < best:
            best_x, best = x, s
    return best_x


def lower_upper_tail(sorted_values: Sequence[float], tail: float = TAIL) -> tuple[float, float]:
    """The lower and upper `tail`-quantiles used by the quantile index.

    With n values and m = ceil(tail * n): the lower one is the m-th smallest
    (at least a `tail` share of the values lie at or below it) and the upper
    one the m-th largest (at least a `tail` share lie at or above it). This
    is the definition the sampling guarantee (T5) needs.
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError("no values")
    m = max(1, math.ceil(tail * n))
    return sorted_values[m - 1], sorted_values[n - m]


def analyse_box(values: Sequence[Sequence[Hashable]], phi: Objective,
                anchor: Point) -> BoxAnalysis:
    """Per-knob and joint search on one box, the realised loss, the
    Theorem 1 bound and the interaction index."""
    vals = _validate(values, anchor)
    return analyse_table(vals, remainder_table(vals, phi, anchor))


def analyse_table(values: Sequence[Sequence[Hashable]], table: RemainderTable) -> BoxAnalysis:
    """`analyse_box` on an already evaluated table (phi is not called)."""
    vals = [tuple(v) for v in values]
    xd = per_knob_choice(vals, table)
    xj = joint_choice(vals, table)
    phi_d = table.phi[xd]
    phi_j = table.phi[xj] if xj is not None else None
    loss = phi_d - phi_j if (phi_d is not None and phi_j is not None) else None
    rd = table.remainder.get(xd)
    rj = table.remainder.get(xj) if xj is not None else None
    bound = rd - rj if (loss is not None and rd is not None and rj is not None) else None
    rs = sorted(table.remainder.values())
    if rs:
        osc = rs[-1] - rs[0]
        lo, hi = lower_upper_tail(rs)
        qidx = hi - lo
    else:
        osc = qidx = None
    return BoxAnalysis(
        anchor=table.anchor, phi_anchor=table.phi_anchor, per_knob=xd, joint=xj,
        phi_per_knob=phi_d, phi_joint=phi_j, loss=loss, bound=bound,
        oscillation=osc, quantile_index=qidx, n_points=len(table.phi),
        n_feasible=sum(s is not None for s in table.phi.values()),
        n_remainder=len(rs),
    )


def coordinatewise_minimum(values: Sequence[Sequence[Hashable]], phi: Objective,
                           start: Point, max_rounds: int = 100) -> Point:
    """Iterated one-knob search: from `start`, move each knob in turn to its
    best value with the others held, accepting strict improvements only
    (an infeasible state accepts any feasible move), until a full round
    changes nothing. The result is coordinate-wise minimal when feasible."""
    vals = _validate(values, start)
    x = tuple(start)
    fx = phi(x)
    for _ in range(max_rounds):
        changed = False
        for k, vk in enumerate(vals):
            for v in vk:
                y = x[:k] + (v,) + x[k + 1:]
                fy = phi(y)
                if fy is not None and (fx is None or fy < fx):
                    x, fx, changed = y, fy, True
        if not changed:
            return x
    raise RuntimeError("coordinate-wise search did not settle within max_rounds")


def theorem2_terms(values: Sequence[Sequence[Hashable]], phi: Objective,
                   start: Point) -> dict | None:
    """Theorem 2 on one box: x^C is the iterated one-knob result from `start`;
    with R^C the remainder anchored at x^C, phi(x^C) - phi(x^J) <= -R^C(x^J).

    Returns None when x^C is infeasible or R^C(x^J) is undefined."""
    vals = [tuple(v) for v in values]
    xc = coordinatewise_minimum(vals, phi, start)
    table = remainder_table(vals, phi, xc)
    xj = joint_choice(vals, table)
    if table.phi_anchor is None or xj is None or xj not in table.remainder:
        return None
    rs = sorted(table.remainder.values())
    return {
        "x_c": xc, "x_j": xj,
        "loss": table.phi_anchor - table.phi[xj],
        "bound": -table.remainder[xj],
        "oscillation": rs[-1] - rs[0],
    }


def sample_range(table: RemainderTable, m: int, rng: random.Random) -> float | None:
    """Range of the remainder over m points drawn uniformly, with
    replacement, from the points where it is defined (T5). Estimating it on a
    system costs m evaluations of phi plus one per value of each knob for
    the axes."""
    pts = list(table.remainder.values())
    if not pts:
        return None
    draws = [pts[rng.randrange(len(pts))] for _ in range(m)]
    return max(draws) - min(draws)


def sampling_confidence(m: int, tail: float = TAIL) -> float:
    """Lower bound on the probability that the range of m sampled remainders
    is at least the quantile index (T5): 1 - 2 (1 - tail)^m."""
    if m < 1:
        raise ValueError("m must be positive")
    return 1.0 - 2.0 * (1.0 - tail) ** m


def tight_instance(rho: float, eta: float) -> tuple[list[tuple], Objective, Point]:
    """The two-knob instance of Proposition T3: per-knob search loses
    rho - eta while the index is rho, for any 0 < eta < rho."""
    if not 0.0 < eta < rho:
        raise ValueError("need 0 < eta < rho")
    table = {(0, 0): 0.0, (1, 0): -eta, (0, 1): -eta, (1, 1): rho - 2.0 * eta}
    return [[0, 1], [0, 1]], table.__getitem__, (0, 0)
