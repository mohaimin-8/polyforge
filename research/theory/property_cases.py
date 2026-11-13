"""Random boxes and objectives for the WP3 property checks, and an
independent numpy oracle for every quantity the checks compare.

Programme 2, work packages WP3 (property tests) and WP18 (T3, T5). The
statements are in INDEX_THEORY.md and are drafts (not yet read by a second
person). This module draws the cases and checks one case at a time;
`property_check.py` runs 100,000 of them and writes PROPERTY_CHECKS.md, and
`test_property_checks.py` runs a seeded sample in seconds.

Seeded numpy rather than Hypothesis. Hypothesis is installed neither here
nor in CI, and the existing suite is seeded and dependency-free. With seeds,
the 100,000-case record can be reproduced case by case: case i is always
drawn from seed (BASE_SEED, i), however the run is split into chunks. The
cases also need structured generators (constructions that reach the bound,
budget constraints that couple the knobs, value labels of several types)
that a strategy library would not supply by itself. Shrinking a failing
case, which Hypothesis would add, is done for T4 by its own search
(t4_check.py).

A case is an array `phi` over the box in index space (NaN marks an
infeasible point), the value labels of each knob (list order = index order),
an anchor and a start for the coordinate-wise search. The library under test
(`interaction_index`) sees only labels and an objective returning a float or
None; the oracle works on the array and shares no code with it.
"""

from __future__ import annotations

import itertools
import math
import random
from collections import Counter
from dataclasses import dataclass

import numpy as np

from interaction_index import (
    analyse_box,
    analyse_table,
    coordinatewise_minimum,
    lower_upper_tail,
    remainder_table,
    sample_range,
    sampling_confidence,
    theorem2_terms,
)

BASE_SEED = 20261113
SIZE_MIN, SIZE_MAX = 2, 17  # values per knob, as the plan states
K_MIN, K_MAX = 2, 4  # knobs per box
MAX_POINTS = 1200  # cap on the number of points of a box (keeps 100,000 cases affordable)
TOL_REL = 1e-12  # tolerance relative to max(1, max |phi|): far above rounding, far below every gap
M_DRAWS = (1, 5, 20, 59, 100)  # sample sizes at which the T5 coverage is checked exactly
CAP_EQUILIBRIA = 8  # coordinate-wise minima per case checked by the oracle (Theorem 2)
CAP_JOINT = 8  # joint minimisers per coordinate-wise minimum
MC_ALPHA = 1e-6  # exact binomial test of sample_range against the exact coverage, per box

FAMILIES = (
    "gauss", "pairwise", "ties", "spike", "transform", "offset",
    "tight_t1", "tight_t1_ties", "tight_t2", "tight_t2_ties",
    "budget", "holes", "bad_anchor", "sparse",
)
FAMILY_NOTES = {
    "gauss": "additive N(0,1) main effects + iid N(0,1) interaction scaled by 0, 1e-6, ..., 10",
    "pairwise": "additive + smooth bilinear pairwise interaction, size log-uniform in [1e-3, 10]",
    "ties": "integer main effects in {-2..2} + sparse integer interaction: many ties",
    "spike": "additive + one spike or dip of size log-uniform in [1e-2, 1e2] at a random point",
    "transform": "square, abs, minus square, tanh or hinge of an additive score: interaction everywhere",
    "offset": "gauss + an offset of 1e3 to 1e9: floating-point stress",
    "tight_t1": "T3 gadget on two knobs inside a random box: loss = rho - eta, index = rho",
    "tight_t1_ties": "the same with eta = 0 and the improving value first in list order: loss = index",
    "tight_t2": "coordinate-wise minimum with -R^C(x^J) = index = rho, loss = rho - 2 eta",
    "tight_t2_ties": "the same with eta = 0: loss = -R^C(x^J) = index",
    "budget": "gauss or pairwise + a knapsack budget: infeasible combinations (constraint coupling)",
    "holes": "gauss + iid infeasible points with probability 0.05, 0.2, 0.5 or 0.9",
    "bad_anchor": "gauss + an infeasible anchor or infeasible axis points",
    "sparse": "at most three feasible points (or none)",
}
LABEL_STYLES = ("int", "perm", "str", "float", "tuple")


@dataclass(frozen=True, eq=False)
class Case:
    """One random box and objective. `phi` is in index space; NaN = infeasible."""

    case_id: int
    family: str
    phi: np.ndarray
    labels: tuple  # labels[k][i]: the value of knob k at index i
    anchor: tuple  # index tuple
    start: tuple  # index tuple: where Theorem 2's search starts
    expect: dict | None  # what a constructed tight case must give

    def values(self) -> list[list]:
        return [list(lab) for lab in self.labels]

    def point(self, idx: tuple) -> tuple:
        return tuple(self.labels[k][i] for k, i in enumerate(idx))

    def index_of(self, point: tuple) -> tuple:
        return tuple(self.labels[k].index(v) for k, v in enumerate(point))

    def objective(self):
        """The objective the library sees: a dict lookup, None if infeasible."""
        flat = self.phi.ravel().tolist()
        table = {p: (None if v != v else v)
                 for p, v in zip(itertools.product(*self.labels), flat)}
        return table.__getitem__


# ---------------------------------------------------------------- drawing

def _bcast(vec: np.ndarray, k: int, n_knobs: int) -> np.ndarray:
    return np.reshape(vec, [-1 if j == k else 1 for j in range(n_knobs)])


def _outer_sum(parts: list[np.ndarray]) -> np.ndarray:
    total = 0.0
    for k, part in enumerate(parts):
        total = total + _bcast(part, k, len(parts))
    return np.asarray(total, dtype=float)


def _indicator(sizes: tuple, k: int, i: int) -> np.ndarray:
    vec = np.zeros(sizes[k])
    vec[i] = 1.0
    return _bcast(vec, k, len(sizes))


def draw_sizes(rng: np.random.Generator) -> tuple:
    """2-4 knobs with 2-17 values each, at most MAX_POINTS points."""
    n_knobs = int(rng.integers(K_MIN, K_MAX + 1))
    while True:
        sizes = tuple(int(s) for s in rng.integers(SIZE_MIN, SIZE_MAX + 1, n_knobs))
        if math.prod(sizes) <= MAX_POINTS:
            return sizes


def draw_labels(rng: np.random.Generator, sizes: tuple) -> tuple:
    """Distinct value labels per knob, of a random type and in random order."""
    style = LABEL_STYLES[int(rng.integers(len(LABEL_STYLES)))]
    labels = []
    for k, s in enumerate(sizes):
        perm = [int(j) for j in rng.permutation(s)]
        if style == "int":
            lab = list(range(s))
        elif style == "perm":
            lab = [3 * j - 7 for j in perm]
        elif style == "str":
            lab = [f"v{j}" for j in perm]
        elif style == "float":
            lab = [0.5 * j + 0.25 for j in perm]
        else:
            lab = [(k, j) for j in perm]
        labels.append(tuple(lab))
    return tuple(labels)


def _anchor(rng: np.random.Generator, sizes: tuple) -> tuple:
    return tuple(int(rng.integers(s)) for s in sizes)


def _mains(rng: np.random.Generator, sizes: tuple) -> list[np.ndarray]:
    return [rng.standard_normal(s) for s in sizes]


def _gauss(rng, sizes):
    sigma = float(rng.choice([0.0, 1e-6, 1e-3, 1e-2, 1e-1, 1.0, 10.0]))
    phi = _outer_sum(_mains(rng, sizes)) + sigma * rng.standard_normal(sizes)
    return phi, _anchor(rng, sizes), None


def _pairwise(rng, sizes):
    n = len(sizes)
    phi = _outer_sum(_mains(rng, sizes))
    sigma = 10.0 ** rng.uniform(-3, 1)
    for j, k in itertools.combinations(range(n), 2):
        u = np.cumsum(rng.standard_normal(sizes[j]))
        v = np.cumsum(rng.standard_normal(sizes[k]))
        phi = phi + sigma * rng.standard_normal() * (_bcast(u, j, n) * _bcast(v, k, n))
    return phi, _anchor(rng, sizes), None


def _ties(rng, sizes):
    mains = [rng.integers(-2, 3, s).astype(float) for s in sizes]
    inter = rng.integers(-1, 2, sizes) * (rng.random(sizes) < 0.3)
    return _outer_sum(mains) + inter, _anchor(rng, sizes), None


def _spike(rng, sizes):
    phi = _outer_sum(_mains(rng, sizes))
    phi[_anchor(rng, sizes)] += float(rng.choice([-1.0, 1.0])) * 10.0 ** rng.uniform(-2, 2)
    return phi, _anchor(rng, sizes), None


def _transform(rng, sizes):
    score = _outer_sum(_mains(rng, sizes))
    kind = int(rng.integers(5))
    phi = [score ** 2, np.abs(score), -score ** 2, 3.0 * np.tanh(score), np.maximum(score, 0.0)][kind]
    return phi, _anchor(rng, sizes), None


def _offset(rng, sizes):
    phi, anchor, _ = _gauss(rng, sizes)
    return phi + float(rng.choice([-1.0, 1.0])) * 10.0 ** rng.uniform(3, 9), anchor, None


def _tight_t1(rng, sizes, ties: bool):
    """Proposition T3 on knobs p, q of a random box: each of p, q alone
    improves by eta (or ties at eta = 0, the improving value listed first),
    both together cost rho. Other values are worse than the anchor."""
    p, q = (int(v) for v in rng.choice(len(sizes), 2, replace=False))
    anchor = list(_anchor(rng, sizes))
    rho = 10.0 ** rng.uniform(-3, 3)
    eta = 0.0 if ties else rho * rng.uniform(0.001, 0.999)
    mains, moved = [], {}
    for k, s in enumerate(sizes):
        main = rng.uniform(0.5, 2.0, s) * rho
        if k in (p, q):
            if ties:  # the anchor value must come after the tying value in list order
                anchor[k] = int(rng.integers(1, s))
                moved[k] = int(rng.integers(0, anchor[k]))
            else:
                moved[k] = int(rng.choice([i for i in range(s) if i != anchor[k]]))
            main[moved[k]] = -eta
        main[anchor[k]] = 0.0
        mains.append(main)
    phi = _outer_sum(mains) + rho * _indicator(sizes, p, moved[p]) * _indicator(sizes, q, moved[q])
    expect = {"t1": {"loss": rho - eta, "bound": rho, "index": rho}}
    return phi, tuple(anchor), expect


def _tight_t2(rng, sizes, ties: bool):
    """Theorem 2 at equality: x^C = the anchor is coordinate-wise minimal
    (single moves cost >= 0, the two special ones cost eta), the joint
    minimum moves knobs p and q together and gains rho."""
    p, q = (int(v) for v in rng.choice(len(sizes), 2, replace=False))
    anchor = _anchor(rng, sizes)
    rho = 10.0 ** rng.uniform(-3, 3)
    eta = 0.0 if ties else rho * rng.uniform(0.001, 0.49)
    mains, moved = [], {}
    for k, s in enumerate(sizes):
        main = rng.uniform(0.5, 2.0, s) * rho
        if k in (p, q):
            moved[k] = int(rng.choice([i for i in range(s) if i != anchor[k]]))
            main[moved[k]] = eta
        main[anchor[k]] = 0.0
        mains.append(main)
    phi = _outer_sum(mains) - rho * _indicator(sizes, p, moved[p]) * _indicator(sizes, q, moved[q])
    expect = {"t2": {"loss": rho - 2.0 * eta, "bound": rho, "index": rho}}
    return phi, anchor, expect


def _budget(rng, sizes):
    phi, anchor, _ = (_gauss if rng.random() < 0.5 else _pairwise)(rng, sizes)
    total = _outer_sum([rng.uniform(0.0, 1.0, s) for s in sizes])
    slack = float(total.max() - total[anchor])
    budget = float(total[anchor]) + slack * rng.uniform(0.0, 1.0) * float(rng.choice([0.2, 0.5, 1.0]))
    return np.where(total > budget, np.nan, phi), anchor, None


def _holes(rng, sizes):
    phi, anchor, _ = _gauss(rng, sizes)
    p = float(rng.choice([0.05, 0.2, 0.5, 0.9]))
    return np.where(rng.random(sizes) < p, np.nan, phi), anchor, None


def _bad_anchor(rng, sizes):
    phi, anchor, _ = _gauss(rng, sizes)
    phi = phi.copy()
    if rng.random() < 0.5:
        phi[anchor] = np.nan
    else:
        for k, s in enumerate(sizes):
            for i in range(s):
                if i != anchor[k] and rng.random() < 0.5:
                    phi[anchor[:k] + (i,) + anchor[k + 1:]] = np.nan
    return phi, anchor, None


def _sparse(rng, sizes):
    phi, anchor, _ = _gauss(rng, sizes)
    keep = np.zeros(sizes, dtype=bool)
    for _ in range(int(rng.integers(0, 4))):
        keep[_anchor(rng, sizes)] = True
    return np.where(keep, phi, np.nan), anchor, None


_DRAW = {
    "gauss": _gauss, "pairwise": _pairwise, "ties": _ties, "spike": _spike,
    "transform": _transform, "offset": _offset,
    "tight_t1": lambda rng, s: _tight_t1(rng, s, ties=False),
    "tight_t1_ties": lambda rng, s: _tight_t1(rng, s, ties=True),
    "tight_t2": lambda rng, s: _tight_t2(rng, s, ties=False),
    "tight_t2_ties": lambda rng, s: _tight_t2(rng, s, ties=True),
    "budget": _budget, "holes": _holes, "bad_anchor": _bad_anchor, "sparse": _sparse,
}


def make_case(case_id: int) -> Case:
    """Case `case_id`: its family cycles through FAMILIES; everything else is
    drawn from seed (BASE_SEED, case_id)."""
    rng = np.random.default_rng([BASE_SEED, case_id])
    family = FAMILIES[case_id % len(FAMILIES)]
    sizes = draw_sizes(rng)
    labels = draw_labels(rng, sizes)
    phi, anchor, expect = _DRAW[family](rng, sizes)
    phi = np.asarray(phi, dtype=float)
    if family.startswith("tight") or rng.random() < 0.5:
        start = anchor
    else:
        start = _anchor(rng, sizes)
    return Case(case_id, family, phi, labels, anchor, start, expect)


# ---------------------------------------------------------------- oracle

def anchored_remainder(phi: np.ndarray, anchor: tuple) -> np.ndarray:
    """R anchored at `anchor` (NaN where undefined): (phi - phi0) minus the
    left-to-right sum of the main effects. The library's Python `sum` uses
    compensated (Neumaier) summation from Python 3.12 on, so the two agree to
    rounding, not bit for bit; comparisons use TOL_REL."""
    n = phi.ndim
    phi0 = phi[anchor]
    if np.isnan(phi0):
        return np.full(phi.shape, np.nan)
    acc = 0.0
    for k in range(n):
        axis = phi[anchor[:k] + (slice(None),) + anchor[k + 1:]]
        acc = acc + _bcast(axis - phi0, k, n)
    return (phi - phi0) - acc


@dataclass(frozen=True, eq=False)
class Oracle:
    per_knob: tuple
    joint: tuple | None
    remainder: np.ndarray
    phi0: float
    loss: float | None
    bound: float | None
    oscillation: float | None
    quantile_index: float | None
    n_remainder: int
    applies: bool


def oracle_t1(phi: np.ndarray, anchor: tuple) -> Oracle:
    """Theorem 1's quantities computed directly on the array."""
    n = phi.ndim
    per_knob = []
    for k in range(n):
        axis = phi[anchor[:k] + (slice(None),) + anchor[k + 1:]]
        per_knob.append(anchor[k] if np.isnan(axis).all() else int(np.nanargmin(axis)))
    xd = tuple(per_knob)
    feasible = ~np.isnan(phi)
    xj = (tuple(int(i) for i in np.unravel_index(int(np.nanargmin(phi)), phi.shape))
          if feasible.any() else None)
    rem = anchored_remainder(phi, anchor)
    defined = rem[~np.isnan(rem)]
    osc = qidx = None
    if defined.size:
        osc = float(defined.max() - defined.min())
        srt = np.sort(defined)
        m = (srt.size + 19) // 20  # exact integer ceil(0.05 n)
        qidx = float(srt[srt.size - m] - srt[m - 1])
    phi_d = phi[xd]
    phi_j = phi[xj] if xj is not None else np.nan
    loss = float(phi_d - phi_j) if not (np.isnan(phi_d) or np.isnan(phi_j)) else None
    rd = rem[xd]
    rj = rem[xj] if xj is not None else np.nan
    bound = float(rd - rj) if loss is not None and not (np.isnan(rd) or np.isnan(rj)) else None
    applies = bool(not np.isnan(phi[anchor]) and loss is not None and bound is not None)
    return Oracle(xd, xj, rem, float(phi[anchor]), loss, bound, osc, qidx, int(defined.size), applies)


def coordinatewise_minimal_mask(phi: np.ndarray) -> np.ndarray:
    """Feasible points where no change of one knob strictly lowers phi."""
    feasible = ~np.isnan(phi)
    filled = np.where(feasible, phi, np.inf)
    mask = feasible.copy()
    for k in range(phi.ndim):
        mask &= filled <= filled.min(axis=k, keepdims=True)
    return mask


def exact_coverage(values, d: float, m_list=M_DRAWS) -> dict:
    """P(range of m uniform draws with replacement >= d), exactly, for every
    m in `m_list`; the range and its comparison with d use the same floating
    point operations as `sample_range(...) >= d`.

    P(range < d) = sum over distinct values u_i of P(all draws v satisfy
    u_i <= v and v - u_i < d) - P(all draws satisfy u_i < v and v - u_i < d):
    the probability that the smallest draw is u_i and the range is short."""
    vals = np.asarray(values, dtype=float)
    n = vals.size
    if n == 0:
        raise ValueError("no values")
    if not d > 0.0:
        return {m: 1.0 for m in m_list}
    u, counts = np.unique(vals, return_counts=True)
    cum = np.concatenate([[0], np.cumsum(counts)])
    s = u.size
    own = np.arange(s)
    j = np.searchsorted(u, u + d, side="left")
    for _ in range(64):  # correct to the exact floating predicate u_j - u_i >= d
        right = (j < s) & (u[np.minimum(j, s - 1)] - u < d)
        left = (j > own + 1) & (u[np.maximum(j - 1, 0)] - u >= d)
        if not (right.any() or left.any()):
            break
        j = j + right.astype(int) - left.astype(int)
    else:
        raise RuntimeError("boundary correction did not settle")
    closed = (cum[j] - cum[own]) / n
    opened = (cum[j] - cum[own + 1]) / n
    return {m: float(1.0 - np.sum(closed ** m - opened ** m)) for m in m_list}


# ---------------------------------------------------------------- checks

def _tol(phi: np.ndarray) -> float:
    finite = phi[~np.isnan(phi)]
    return TOL_REL * max(1.0, float(np.abs(finite).max())) if finite.size else TOL_REL


def _close(a, b, tol: float) -> bool:
    """Agreement of two optional floats (None = undefined) to `tol`."""
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def _t1_mismatches(case: Case, table, a, o: Oracle, tol: float) -> list[str]:
    """Where the library and the oracle disagree on Theorem 1's quantities."""
    out = []
    if case.index_of(a.per_knob) != o.per_knob:
        out.append("per_knob")
    if (a.joint is None) != (o.joint is None) or (a.joint is not None and case.index_of(a.joint) != o.joint):
        out.append("joint")
    for name in ("loss", "bound", "oscillation", "quantile_index"):
        if not _close(getattr(a, name), getattr(o, name), tol):
            out.append(name)
    if a.n_remainder != o.n_remainder or a.theorem1_applies != o.applies:
        out.append("counts_or_applies")
    lib = np.array([table.remainder.get(p, np.nan) for p in itertools.product(*case.labels)],
                   dtype=float).reshape(case.phi.shape)
    if not np.array_equal(np.isnan(lib), np.isnan(o.remainder)):
        out.append("remainder_domain")
    elif not np.all(np.abs(np.nan_to_num(lib) - np.nan_to_num(o.remainder)) <= tol):
        out.append("remainder")
    return out


def _check_t1(case: Case, fn, c: Counter, ex: list, worst: dict, tol: float) -> tuple:
    values = case.values()
    table = remainder_table(values, fn, case.point(case.anchor))
    a = analyse_table(values, table)
    o = oracle_t1(case.phi, case.anchor)
    if case.case_id % 10 == 0:  # the public entry point gives the same analysis
        b = analyse_box(values, fn, case.point(case.anchor))
        if b != a:
            c["analyse_box_differs"] += 1
            ex.append(f"case {case.case_id}: analyse_box differs from analyse_table")
    mism = _t1_mismatches(case, table, a, o, tol)
    if mism:
        c["t1_oracle_mismatch"] += 1
        ex.append(f"case {case.case_id} ({case.family}): library and oracle differ on {mism}")
    if a.loss is not None:
        c["t1_loss_defined"] += 1
        if a.loss < -tol:
            c["t1_violation"] += 1
            ex.append(f"case {case.case_id}: negative loss {a.loss}")
    if a.theorem1_applies:
        c["t1_applies"] += 1
        if a.loss > a.bound + tol or a.bound > a.oscillation + tol:
            c["t1_violation"] += 1
            ex.append(f"case {case.case_id}: loss {a.loss}, bound {a.bound}, index {a.oscillation}")
        if a.loss > tol:
            c["t1_costly"] += 1
            if a.oscillation > 0.0:
                ratio = a.loss / a.oscillation
                worst["t1_max_loss_over_index"] = max(worst.get("t1_max_loss_over_index", 0.0), ratio)
            if a.loss >= a.oscillation - tol:
                c["t1_loss_equals_index"] += 1
    elif a.loss is not None and a.oscillation is not None:
        c["t1_outside_hypotheses"] += 1  # anchor, x^D, x^J feasible but a remainder undefined
        if a.loss > a.oscillation + tol:
            c["t1_outside_loss_above_index"] += 1
    if a.phi_anchor is not None and a.phi_per_knob is None and a.phi_joint is not None:
        c["t1_constraint_coupling"] += 1
    if a.phi_anchor is None:
        c["t1_anchor_infeasible"] += 1
    if a.joint is None:
        c["t1_no_feasible_point"] += 1
    exp = (case.expect or {}).get("t1")
    if exp is not None:
        c["tight_t1_cases"] += 1
        got = {"loss": a.loss, "bound": a.bound, "index": a.oscillation}
        if any(got[k] is None or abs(got[k] - v) > tol for k, v in exp.items()):
            c["tight_mismatch"] += 1
            ex.append(f"case {case.case_id}: tight T1 expected {exp}, got {got}")
    return table, a


def _check_t2_library(case: Case, fn, c: Counter, ex: list, tol: float) -> None:
    values = case.values()
    start = case.point(case.start)
    try:
        xc_pt = coordinatewise_minimum(values, fn, start)
        terms = theorem2_terms(values, fn, start)
    except RuntimeError:
        c["t2_search_not_settled"] += 1
        return
    xc = case.index_of(xc_pt)
    cw = coordinatewise_minimal_mask(case.phi)
    if np.isnan(case.phi[xc]):
        c["t2_search_ends_infeasible"] += 1
        stuck = all(np.isnan(case.phi[xc[:k] + (slice(None),) + xc[k + 1:]]).all()
                    for k in range(case.phi.ndim))
        if not stuck or terms is not None:
            c["t2_oracle_mismatch"] += 1
            ex.append(f"case {case.case_id}: search ended at an infeasible point with a feasible move")
        return
    if not cw[xc]:
        c["t2_not_coordinatewise_minimal"] += 1
        ex.append(f"case {case.case_id}: search result {xc} is not coordinate-wise minimal")
    rem = anchored_remainder(case.phi, xc)
    xj = tuple(int(i) for i in np.unravel_index(int(np.nanargmin(case.phi)), case.phi.shape))
    if np.isnan(rem[xj]):
        c["t2_remainder_undefined"] += 1
        if terms is not None:
            c["t2_oracle_mismatch"] += 1
        return
    defined = rem[~np.isnan(rem)]
    want = {"loss": float(case.phi[xc] - case.phi[xj]), "bound": float(-rem[xj]),
            "oscillation": float(defined.max() - defined.min())}
    if terms is None or any(not abs(terms[k] - v) <= tol for k, v in want.items()):
        c["t2_oracle_mismatch"] += 1
        ex.append(f"case {case.case_id}: theorem2_terms {terms} vs oracle {want}")
        return
    c["t2_applies"] += 1
    if terms["loss"] < -tol or terms["loss"] > terms["bound"] + tol or terms["bound"] > terms["oscillation"] + tol:
        c["t2_violation"] += 1
        ex.append(f"case {case.case_id}: Theorem 2 fails: {terms}")
    exp = (case.expect or {}).get("t2")
    if exp is not None:
        c["tight_t2_cases"] += 1
        got = {"loss": terms["loss"], "bound": terms["bound"], "index": terms["oscillation"]}
        if any(abs(got[k] - v) > tol for k, v in exp.items()):
            c["tight_mismatch"] += 1
            ex.append(f"case {case.case_id}: tight T2 expected {exp}, got {got}")


def _check_t2_all_equilibria(case: Case, c: Counter, ex: list, tol: float) -> None:
    """Theorem 2 for every coordinate-wise minimum (up to CAP_EQUILIBRIA) and
    every joint minimiser (up to CAP_JOINT), independent of the search code."""
    phi = case.phi
    if np.isnan(phi).all():
        return
    best = np.nanmin(phi)
    joints = [tuple(int(i) for i in j) for j in np.argwhere(phi == best)[:CAP_JOINT]]
    for xc in np.argwhere(coordinatewise_minimal_mask(phi))[:CAP_EQUILIBRIA]:
        xc = tuple(int(i) for i in xc)
        rem = anchored_remainder(phi, xc)
        defined = rem[~np.isnan(rem)]
        osc = float(defined.max() - defined.min())
        c["t2_equilibria"] += 1
        for xj in joints:
            if np.isnan(rem[xj]):
                continue
            c["t2_equilibrium_checks"] += 1
            loss, bound = float(phi[xc] - phi[xj]), float(-rem[xj])
            if loss < -tol or loss > bound + tol or bound > osc + tol:
                c["t2_violation"] += 1
                ex.append(f"case {case.case_id}: Theorem 2 fails at x^C={xc}, x^J={xj}")


def binom_two_sided(hits: int, trials: int, p: float) -> float:
    """Exact two-sided binomial p-value (twice the smaller tail, capped at
    1). A normal approximation is wrong here: the coverage is often within
    1e-4 of 1, where one miss in a thousand draws is unremarkable."""
    if p >= 1.0:
        return 1.0 if hits == trials else 0.0
    if p <= 0.0:
        return 1.0 if hits == 0 else 0.0
    lp, lq = math.log(p), math.log1p(-p)
    logpmf = [math.lgamma(trials + 1) - math.lgamma(k + 1) - math.lgamma(trials - k + 1) + k * lp
              + (trials - k) * lq for k in range(trials + 1)]
    pmf = np.exp(np.array(logpmf))
    lower, upper = float(pmf[: hits + 1].sum()), float(pmf[hits:].sum())
    return min(1.0, 2.0 * min(lower, upper))


def _check_t5(case: Case, table, a, c: Counter, ex: list, worst: dict, mc_trials: int) -> None:
    rs = sorted(table.remainder.values())
    if not rs:
        return
    rng = random.Random(BASE_SEED * 1_000_003 + case.case_id)
    lo, hi = lower_upper_tail(rs)
    d = hi - lo
    cov = exact_coverage(rs, d)
    c["t5_boxes"] += 1
    for m, p in cov.items():
        margin = p - sampling_confidence(m)
        key = f"t5_min_margin_m{m}"
        worst[key] = min(worst.get(key, math.inf), margin)
        if margin < -1e-12:
            c["t5_below_bound"] += 1
            ex.append(f"case {case.case_id}: exact coverage {p} < bound {sampling_confidence(m)} at m={m}")
    worst["t5_min_coverage_m59"] = min(worst.get("t5_min_coverage_m59", math.inf), cov[59])
    draws = [sample_range(table, 59, rng) for _ in range(max(1, mc_trials))]
    c["t5_draws"] += len(draws)
    if max(draws) > a.oscillation:
        c["t5_sample_above_index"] += 1
        ex.append(f"case {case.case_id}: sampled range above the index")
    if mc_trials:
        p = cov[59]
        hits = sum(r >= d for r in draws)
        pval = binom_two_sided(hits, mc_trials, p)
        c["t5_mc_boxes"] += 1
        c["t5_mc_misses"] += mc_trials - hits
        worst["t5_mc_min_pvalue"] = min(worst.get("t5_mc_min_pvalue", 1.0), pval)
        worst["t5_mc_min_empirical"] = min(worst.get("t5_mc_min_empirical", math.inf), hits / mc_trials)
        if pval < MC_ALPHA:
            c["t5_mc_disagree"] += 1
            ex.append(f"case {case.case_id}: Monte Carlo coverage {hits / mc_trials} vs exact {p} "
                      f"(two-sided binomial p = {pval:.2g})")


def check_case(case: Case, mc_trials: int = 0) -> tuple[Counter, list, dict]:
    """Every WP3 check on one case: Theorem 1 (library against oracle),
    Theorem 2 (the library's search and every coordinate-wise minimum), the
    tight constructions, and T5 (exact coverage, sampled range <= index,
    optionally a Monte Carlo cross-check of `sample_range`). Returns counters,
    failure descriptions and extreme values."""
    c, ex, worst = Counter(), [], {}
    tol = _tol(case.phi)
    c["cases"] += 1
    fn = case.objective()
    table, a = _check_t1(case, fn, c, ex, worst, tol)
    _check_t2_library(case, fn, c, ex, tol)
    _check_t2_all_equilibria(case, c, ex, tol)
    _check_t5(case, table, a, c, ex, worst, mc_trials)
    return c, ex, worst


def describe_box(case: Case) -> dict:
    """Shape facts for the coverage table of the report."""
    return {"knobs": case.phi.ndim, "points": int(case.phi.size),
            "max_values": int(max(case.phi.shape)), "min_values": int(min(case.phi.shape)),
            "infeasible": bool(np.isnan(case.phi).any())}


# ---------------------------------------------------------------- T3 and T5 constructions

def draw_rho_eta(i: int) -> tuple[float, float]:
    """A random (rho, eta) with 0 < eta < rho: rho log-uniform over twelve
    decades, eta/rho uniform, or within 1e-12 of 0, or within 1e-12 of 1."""
    rng = np.random.default_rng([BASE_SEED, 3, i])
    rho = 10.0 ** rng.uniform(-6, 6)
    while True:
        kind = int(rng.integers(3))
        if kind == 0:
            u = rng.uniform(0.0, 1.0)
        elif kind == 1:
            u = 10.0 ** -rng.uniform(0, 12)
        else:
            u = 1.0 - 10.0 ** -rng.uniform(1, 12)
        eta = u * rho
        if 0.0 < eta < rho:
            return rho, eta


def check_t3(rho: float, eta: float) -> list[str]:
    """Proposition T3's instance: per-knob search takes (1, 1), the joint
    optimum is -eta, the loss is rho - eta, the index and the bound are rho."""
    from interaction_index import tight_instance
    values, phi, anchor = tight_instance(rho, eta)
    a = analyse_box(values, phi, anchor)
    tol = TOL_REL * rho
    fails = []
    if a.per_knob != (1, 1):
        fails.append("per_knob")
    if a.joint not in ((0, 1), (1, 0)) or not a.theorem1_applies:
        fails.append("joint_or_applies")
    for name, want in (("loss", rho - eta), ("oscillation", rho), ("bound", rho)):
        if not abs(getattr(a, name) - want) <= tol:
            fails.append(name)
    return fails


def tie_instance(rho: float):
    """T3 with eta = 0 and each knob's improving value listed first: the
    per-knob tie-break takes (1, 1) and the loss equals the index exactly."""
    table = {(0, 0): 0.0, (1, 0): 0.0, (0, 1): 0.0, (1, 1): rho}
    return [[1, 0], [1, 0]], table.__getitem__, (0, 0)


def hardest_table(n: int) -> list[float]:
    """n remainder values with exactly ceil(0.05 n) at the top, as many at the
    bottom and the rest between: the table on which T5's bound is nearly tight."""
    k = (n + 19) // 20
    if 2 * k > n:
        raise ValueError("n too small for two disjoint tails")
    return [1.0] * k + [0.0] * (n - 2 * k) + [-1.0] * k


def hardest_table_coverage(n: int, m: int) -> tuple[float, float]:
    """(exact coverage on hardest_table(n), closed form 1 - 2(1-p)^m + (1-2p)^m)."""
    vals = sorted(hardest_table(n))
    lo, hi = lower_upper_tail(vals)
    p = ((n + 19) // 20) / n
    return exact_coverage(vals, hi - lo, (m,))[m], 1.0 - 2.0 * (1.0 - p) ** m + (1.0 - 2.0 * p) ** m
