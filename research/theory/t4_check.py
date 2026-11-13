"""T4 (WP18, stretch): is the run-level excess of per-knob control over
joint control at most a constant times the summed interaction index?
Tested on random instances before any proof attempt, as the plan requires.

Formal setting (INDEX_THEORY.md, section T4, states the same):

- Grid G = {0..N-1}^K. Box radius r: B(x) = {y in G : |y - x|_inf <= r}.
- Objectives phi_0..phi_{T-1} on G, every point feasible, each with a
  unique minimiser m_t.
- Joint step J_t(x): the first minimiser of phi_t over B(x) in product order
  (`interaction_index.joint_choice` on that box). Per-knob step D_t(x): each
  knob's first minimiser along its axis through x inside B(x)
  (`per_knob_choice`). Index I_t(x): max R - min R over B(x), R anchored at x.
- Runs from a common x_0: x^J_{t+1} = J_t(x^J_t), x^D_{t+1} = D_t(x^D_t).
  Excess E_T = sum_{t<T} phi_t(x^D_{t+1}) - phi_t(x^J_{t+1}).

Assumptions of the plan, as formalised here, for a metric d in {l_inf, l_1}:

- (a) |m_{t+1} - m_t|_inf <= r (the minimiser moves at most one box radius).
- (b) alpha = min_t min_{x != m_t} (phi_t(x) - phi_t(m_t)) / d(x, m_t) and
  beta = max_t max_{d(x,y)=1} |phi_t(x) - phi_t(y)|: the tightest growth and
  per-step slopes (l_inf neighbours are king moves, l_1 neighbours unit moves).
- (c) for every t and EVERY x in G, d(J_t(x), m_t) <= d(x, m_t) and
  d(D_t(x), m_t) <= d(x, m_t) (a greedy step never moves away).

Conjecture tested ("target"): E_T' <= (1 + beta/alpha) sum_{t<T'} I_t for
every prefix T' of the run, with I_t the index along the per-knob run
(I_t(x^D_t)); separately with the index along the joint run (I_t(x^J_t)).
Testing with the tightest alpha and beta gives the smallest constant the
conjecture allows.

Also checked: T4a, the two-step proposition (start within reach of m_0, (a)
and (b) only): E_1 <= I_0 and E_2 <= (1 + beta/alpha) I_0 + I_1; and T4',
the within-reach theorem (draft): if |x_0 - m_0|_inf <= r, the minimiser
moves at most rho <= r per step, and I_t(x) <= alpha_inf (r - rho) at every
state x within reach of m_t, then both runs stay within reach, the joint run
lands on m_t at every step, and 0 <= E_T' <= sum_{t<T'} I_t(x^D_t).

    python t4_check.py target --start 0 --draws 40000     # repeat with later starts
    python t4_check.py within --start 0 --draws 40000
    python t4_check.py minimal                            # smallest counterexample search
    python t4_check.py drift                              # the explicit family, T = 2..24
    python property_check.py report                       # writes PROPERTY_CHECKS.md

Results accumulate in data/t4_checks.json.
"""

from __future__ import annotations

import argparse
import functools
import itertools
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

RESULTS = HERE / "data" / "t4_checks.json"
BASE_SEED_T4 = 20261114
KIND_CODES = {"target": 1, "within": 2, "minimal": 3}
N_ACCEPTED = 100_000
TOL_REL = 1e-9
SHAPES = ("max", "l2", "valley", "ridge", "neg_max", "neg_l2", "random", "none")
SCHEDULES = ("always", "first", "random", "single")
PATHS = ("walk", "drift", "still", "adversarial")
# T4' draws add "bump" (one raised point next to the minimiser, which can
# fool one axis search while the growth slope stays) and favour it: under
# (e) the other shapes never made the runs separate
WITHIN_SHAPES = SHAPES + ("bump",)
WITHIN_SHAPE_P = (0.05, 0.05, 0.15, 0.15, 0.05, 0.05, 0.1, 0.05, 0.35)
METRICS = ("linf", "l1")
MAX_LISTED = 25


@dataclass(frozen=True, eq=False)
class Instance:
    """One multi-step instance. `phi` has shape (T, N, ..., N)."""

    inst_id: int
    kind: str
    family: dict
    r: int
    phi: np.ndarray
    minimisers: np.ndarray  # (T, K)
    x0: tuple

    @property
    def shape(self) -> tuple:
        t, *rest = self.phi.shape
        return t, len(rest), rest[0]


# ---------------------------------------------------------------- geometry

def grid_coords(n_knobs: int, n: int) -> np.ndarray:
    """All points of {0..n-1}^K in C order, shape (n^K, K)."""
    return np.indices((n,) * n_knobs).reshape(n_knobs, -1).T


def dist(z: np.ndarray, metric: str) -> np.ndarray:
    """Distance of offsets z (..., K) from 0."""
    return np.abs(z).max(axis=-1) if metric == "linf" else np.abs(z).sum(axis=-1)


def per_knob_step(phi_t: np.ndarray, x: tuple, r: int) -> tuple:
    """D_t(x) for one state (used while drawing adaptive minimiser paths)."""
    n = phi_t.shape[0]
    out = []
    for k in range(phi_t.ndim):
        best, arg = math.inf, x[k]
        for v in range(max(0, x[k] - r), min(n - 1, x[k] + r) + 1):
            s = phi_t[x[:k] + (v,) + x[k + 1:]]
            if s < best:
                best, arg = s, v
        out.append(arg)
    return tuple(out)


# ---------------------------------------------------------------- drawing

def _shape_term(shape: str, z: np.ndarray, pair: tuple, u: np.ndarray | None,
                bump: tuple | None = None) -> np.ndarray:
    """The interaction part psi(z) of phi_t = sum_k w_k |z_k| + lambda_t psi(z)."""
    i, j = pair
    if shape == "bump":
        return np.all(z == np.array(bump, dtype=float), axis=-1).astype(float)
    if shape == "max":
        return np.abs(z).max(axis=-1)
    if shape == "l2":
        return np.sqrt((z ** 2).sum(axis=-1))
    if shape == "valley":
        return np.abs(z[..., i] - z[..., j])
    if shape == "ridge":
        return np.abs(z[..., i] + z[..., j])
    if shape == "neg_max":
        return -np.abs(z).max(axis=-1)
    if shape == "neg_l2":
        return -np.sqrt((z ** 2).sum(axis=-1))
    if shape == "random":
        return u
    return np.zeros(z.shape[:-1])


def _draw_params(rng: np.random.Generator, kind: str, overrides: dict | None) -> dict:
    o = dict(overrides or {})
    p = {}
    p["K"] = o.get("K", 2 if rng.random() < 0.7 else 3)
    if kind == "within":
        p["shape"] = o.get("shape", WITHIN_SHAPES[int(rng.choice(len(WITHIN_SHAPES), p=WITHIN_SHAPE_P))])
        if p["shape"] == "bump":  # slack r - rho >= 2: the only regime where (e) allows a deviation
            p["r"] = o.get("r", int(rng.integers(3, 5)) if p["K"] == 2 else 2)
            p["speed"] = o.get("speed", int(rng.integers(0, 2)) if p["r"] >= 3 else 0)
        else:
            p["r"] = o.get("r", int(rng.integers(1, 4)) if p["K"] == 2 else int(rng.integers(1, 3)))
            p["speed"] = o.get("speed", int(rng.integers(0, p["r"])))  # rho <= r - 1
        lo_n = 2 * p["r"] + 2
        p["N"] = o.get("N", int(rng.integers(lo_n, lo_n + 7)) if p["K"] == 2 else int(rng.integers(lo_n, lo_n + 2)))
        p["start"] = "reach"
    else:
        p["r"] = o.get("r", int(rng.integers(1, 3)) if p["K"] == 2 else 1)
        p["N"] = o.get("N", int(rng.integers(4, 13)) if p["K"] == 2 else int(rng.integers(4, 8)))
        p["speed"] = p["r"]
        p["start"] = o.get("start", "reach" if rng.random() < 0.5 else "anywhere")
    p["T"] = o.get("T", int(rng.integers(2, 17)) if p["K"] == 2 else int(rng.integers(2, 13)))
    if kind != "within":
        p["shape"] = o.get("shape", SHAPES[int(rng.integers(len(SHAPES)))])
    p["schedule"] = o.get("schedule", SCHEDULES[int(rng.integers(len(SCHEDULES)))])
    p["path"] = o.get("path", PATHS[int(rng.integers(len(PATHS)))])
    p["numbers"] = o.get("numbers", "int" if rng.random() < 0.3 else "real")
    p["weights"] = o.get("weights", "static" if rng.random() < 0.6 else "varying")
    return p


def _weights(rng: np.random.Generator, p: dict) -> np.ndarray:
    if p["numbers"] == "int":
        return rng.integers(1, 4, p["K"]).astype(float)
    return rng.uniform(0.5, 2.0, p["K"])


def _lambda(rng: np.random.Generator, p: dict, w: np.ndarray) -> float:
    if p["shape"] in ("neg_max", "neg_l2", "random"):  # keep phi_t > phi_t(m_t) off the minimiser
        return float(rng.uniform(0.05, 0.9) * w.min())
    if p["numbers"] == "int":
        return float(rng.integers(1, 4))
    return float(w.mean() * 10.0 ** rng.uniform(-1.3, 0.7))


@functools.lru_cache(maxsize=None)
def shape_index_bound(shape: str, n_knobs: int, r: int, bump: tuple | None = None) -> float:
    """An upper bound on the index of the shape term psi at every state within
    reach of the minimiser (|x - m|_inf <= r), from an unclipped reference
    grid; clipping a box at the grid's edge can only lower its index. For the
    random field, |u| <= 1 gives |R| <= 2K, so the index is at most 4K."""
    if shape == "random":
        return 4.0 * n_knobs
    if shape == "none":
        return 0.0
    n = 4 * r + 1
    coords = grid_coords(n_knobs, n).reshape((n,) * n_knobs + (n_knobs,))
    z = (coords - 2 * r).astype(float)
    psi = _shape_term(shape, z, (0, 1), None, bump)[None]
    index = index_all(psi, r).reshape((n,) * n_knobs)
    reach = np.abs(z).max(axis=-1) <= r
    return float(index[reach].max())


def _within_lambda(rng: np.random.Generator, p: dict, weights: list) -> float:
    """lambda for T4' draws: a share theta of the largest value for which (e),
    I_t(x) <= alpha (r - rho), holds by construction (theta in [0.6, 1] for
    bumps, [0.25, 1] otherwise; in [1, 3] for a fifth of the draws, a
    negative control on which (e) may fail). The index of phi_t is
    lambda on_t times the index of psi; alpha >= min w - sigma lambda, with
    sigma = 1 for the negative shapes and the random field, 0 otherwise."""
    slack = p["r"] - p["speed"]
    w_min = float(min(w.min() for w in weights))
    bound = shape_index_bound(p["shape"], p["K"], p["r"], p.get("bump"))
    sigma = 1.0 if p["shape"] in ("neg_max", "neg_l2", "random") else 0.0
    if bound == 0.0:
        return 0.0
    if rng.random() < 0.2:  # negative control: beyond the limit, so (e) may fail
        theta = rng.uniform(1.0, 3.0)
    elif p["shape"] == "bump":
        theta = rng.uniform(0.6, 1.0)
    else:
        theta = rng.uniform(0.25, 1.0)
    return float(theta * w_min * slack / (bound + sigma * slack))


def _schedule(rng: np.random.Generator, p: dict) -> np.ndarray:
    t = p["T"]
    if p["schedule"] == "always":
        return np.ones(t)
    if p["schedule"] == "first":
        return (np.arange(t) < int(rng.integers(1, t))).astype(float)
    if p["schedule"] == "random":
        return (rng.random(t) < 0.5).astype(float)
    on = np.zeros(t)
    on[int(rng.integers(t))] = 1.0
    return on


def draw_instance(inst_id: int, kind: str = "target", overrides: dict | None = None) -> Instance:
    """Instance `inst_id` of `kind`, drawn from seed (BASE_SEED_T4, kind, id).
    The assumptions are NOT guaranteed here; `evaluate` checks them."""
    rng = np.random.default_rng([BASE_SEED_T4, KIND_CODES[kind], inst_id])
    p = _draw_params(rng, kind, overrides)
    p["kind"] = kind
    k, n, r, t_max, s = p["K"], p["N"], p["r"], p["T"], p["speed"]
    coords = grid_coords(k, n).reshape((n,) * k + (k,))
    pair = tuple(int(v) for v in rng.choice(k, 2, replace=False))
    w = _weights(rng, p)
    if kind == "within":  # all weights first: lambda depends on their minimum
        if p["shape"] == "bump":
            while True:  # next to the minimiser
                b = tuple(int(v) for v in rng.integers(-1, 2, k))
                if any(b):
                    break
            p["bump"] = b
        ws = [w] + [_weights(rng, p) if p["weights"] == "varying" else w for _ in range(t_max - 1)]
        lam = _within_lambda(rng, p, ws)
    else:
        lam = _lambda(rng, p, w)
    on = _schedule(rng, p)
    m = tuple(int(v) for v in rng.integers(0, n, k))
    x0 = (tuple(int(np.clip(m[i] + rng.integers(-r, r + 1), 0, n - 1)) for i in range(k))
          if p["start"] == "reach" else tuple(int(v) for v in rng.integers(0, n, k)))
    x0 = tuple(x0)
    if p["path"] == "drift":
        while True:
            direction = rng.integers(-1, 2, k) * s
            if s == 0 or direction.any():
                break
    phis, mins, x_d = [], [], x0
    for t in range(t_max):
        if kind == "within":
            w = ws[t]
        elif p["weights"] == "varying" and t > 0:
            w = _weights(rng, p)
            if p["shape"] in ("neg_max", "neg_l2", "random"):
                lam = min(lam, float(0.9 * w.min()))
        z = (coords - np.array(m)).astype(float)
        u = None
        if p["shape"] == "random":
            u = rng.uniform(-1.0, 1.0, (n,) * k)
            u[m] = 0.0
        phi_t = (np.abs(z) * w).sum(axis=-1) + lam * on[t] * _shape_term(p["shape"], z, pair, u, p.get("bump"))
        phis.append(phi_t)
        mins.append(m)
        if t == t_max - 1:
            break
        if p["path"] == "walk":
            step = rng.integers(-s, s + 1, k)
        elif p["path"] == "drift":
            step = direction
        elif p["path"] == "still":
            step = np.zeros(k, dtype=int)
        else:  # adversarial: away from where the per-knob run is about to be
            x_d = per_knob_step(phi_t, x_d, r)
            sign = np.sign(np.array(m) - np.array(x_d))
            sign = np.where(sign == 0, rng.choice([-1, 1], k), sign)
            step = sign * s
        m = tuple(int(v) for v in np.clip(np.array(m) + step, 0, n - 1))
    family = {key: p[key] for key in ("K", "N", "r", "T", "speed", "start", "shape", "schedule",
                                      "path", "numbers", "weights")}
    family["lambda"] = lam
    return Instance(inst_id, kind, family, r, np.array(phis), np.array(mins), x0)


# ---------------------------------------------------------------- evaluation

def step_maps(phi: np.ndarray, r: int) -> tuple[np.ndarray, np.ndarray]:
    """Joint and per-knob next state of every state at every step, as flat
    indices, arrays (T, N^K). Ties: first in product order / list order,
    strictly smaller wins, as in interaction_index."""
    t_max, n_knobs, n = phi.shape[0], phi.ndim - 1, phi.shape[1]
    pad = np.pad(phi, [(0, 0)] + [(r, r)] * n_knobs, constant_values=np.inf)
    strides = n ** np.arange(n_knobs - 1, -1, -1)
    best = np.full(phi.shape, np.inf)
    off = np.zeros(phi.shape, dtype=np.int64)
    for delta in itertools.product(range(-r, r + 1), repeat=n_knobs):
        cand = pad[(slice(None),) + tuple(slice(r + d, r + d + n) for d in delta)]
        better = cand < best
        best = np.where(better, cand, best)
        off = np.where(better, int(np.dot(delta, strides)), off)
    flat = np.arange(n ** n_knobs)
    joint = flat + off.reshape(t_max, -1)
    pk = np.zeros(phi.shape, dtype=np.int64)
    for k in range(n_knobs):
        bestk = np.full(phi.shape, np.inf)
        offk = np.zeros(phi.shape, dtype=np.int64)
        for d in range(-r, r + 1):
            idx = [slice(None)] + [slice(r, r + n)] * n_knobs
            idx[1 + k] = slice(r + d, r + d + n)
            cand = pad[tuple(idx)]
            better = cand < bestk
            bestk = np.where(better, cand, bestk)
            offk = np.where(better, d * int(strides[k]), offk)
        pk = pk + offk
    return joint, flat + pk.reshape(t_max, -1)


def index_all(phi: np.ndarray, r: int) -> np.ndarray:
    """I_t(x) for every step and state, (T, N^K): max R - min R over B(x)
    with R anchored at x, in the library's order of operations."""
    t_max, n_knobs, n = phi.shape[0], phi.ndim - 1, phi.shape[1]
    pad = np.pad(phi, [(0, 0)] + [(r, r)] * n_knobs, constant_values=np.nan)
    axis = {}
    for k in range(n_knobs):
        for d in range(-r, r + 1):
            idx = [slice(None)] + [slice(r, r + n)] * n_knobs
            idx[1 + k] = slice(r + d, r + d + n)
            axis[k, d] = pad[tuple(idx)] - phi
    rmax = np.zeros(phi.shape)
    rmin = np.zeros(phi.shape)
    for delta in itertools.product(range(-r, r + 1), repeat=n_knobs):
        if not any(delta):
            continue
        acc = 0.0
        for k, d in enumerate(delta):
            acc = acc + axis[k, d]
        rem = (pad[(slice(None),) + tuple(slice(r + d, r + d + n) for d in delta)] - phi) - acc
        rmax = np.fmax(rmax, rem)
        rmin = np.fmin(rmin, rem)
    return (rmax - rmin).reshape(t_max, -1)


def lipschitz(phi: np.ndarray, metric: str) -> float:
    """beta: the largest change of phi_t between neighbouring states."""
    n_knobs, n = phi.ndim - 1, phi.shape[1]
    if metric == "l1":
        dirs = [tuple(int(i == k) for i in range(n_knobs)) for k in range(n_knobs)]
    else:
        dirs = [d for d in itertools.product((-1, 0, 1), repeat=n_knobs)
                if any(d) and d[next(i for i, v in enumerate(d) if v)] > 0]
    best = 0.0
    for d in dirs:
        a = tuple(slice(max(0, v), n + min(0, v)) for v in d)
        b = tuple(slice(max(0, -v), n - max(0, v)) for v in d)
        best = max(best, float(np.abs(phi[(slice(None),) + a] - phi[(slice(None),) + b]).max()))
    return best


def _prefix_check(e: np.ndarray, idx: np.ndarray, const: float, tol: float) -> dict:
    """E_T' <= const * S_T' for every prefix: earliest failing T', worst ratio."""
    big_e, big_s = np.cumsum(e), np.cumsum(idx)
    bad = big_e > const * big_s + tol
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(big_s > tol, big_e / (const * big_s), np.where(big_e > tol, np.inf, 0.0))
    return {"violated": bool(bad.any()), "first": int(np.argmax(bad)) + 1 if bad.any() else None,
            "max_ratio": float(ratio.max()), "E": float(big_e[-1]), "S": float(big_s[-1])}


def evaluate(inst: Instance) -> dict:
    """Assumptions, both runs and every claim on one instance."""
    t_max, n_knobs, n = inst.shape
    r = inst.r
    flat_phi = inst.phi.reshape(t_max, -1)
    coords = grid_coords(n_knobs, n)
    strides = n ** np.arange(n_knobs - 1, -1, -1)
    mins = inst.minimisers
    m_flat = mins @ strides
    tol = TOL_REL * max(1.0, float(np.abs(flat_phi).max()))
    out = {"id": inst.inst_id, "family": inst.family, "T": t_max, "K": n_knobs, "N": n, "r": r}
    rows = np.arange(t_max)
    gap = flat_phi - flat_phi[rows, m_flat][:, None]
    off_min = np.ones_like(gap, dtype=bool)
    off_min[rows, m_flat] = False
    out["unique_minimisers"] = bool((gap[off_min] > tol).all()) if off_min.any() else True
    steps = np.abs(np.diff(mins, axis=0)).max(axis=1) if t_max > 1 else np.zeros(0, dtype=int)
    out["rho"] = int(steps.max()) if steps.size else 0
    out["a"] = out["rho"] <= r
    z_now = coords[None, :, :] - mins[:, None, :]
    joint, pk = step_maps(inst.phi, r)
    slopes, cond_c = {}, {}
    for metric in METRICS:
        d_now = dist(z_now, metric)
        alpha = float((gap[off_min] / d_now[off_min]).min()) if off_min.any() else math.inf
        slopes[metric] = (alpha, lipschitz(inst.phi, metric))
        d_joint = dist(coords[joint] - mins[:, None, :], metric)
        d_pk = dist(coords[pk] - mins[:, None, :], metric)
        cond_c[metric] = bool((d_joint <= d_now).all() and (d_pk <= d_now).all())
    out["alpha"] = {m: slopes[m][0] for m in METRICS}
    out["beta"] = {m: slopes[m][1] for m in METRICS}
    out["c"] = cond_c
    index = index_all(inst.phi, r)
    x0 = int(np.dot(inst.x0, strides))
    xd, xj = [x0], [x0]
    for t in range(t_max):
        xd.append(int(pk[t, xd[-1]]))
        xj.append(int(joint[t, xj[-1]]))
    xd, xj = np.array(xd), np.array(xj)
    e = flat_phi[rows, xd[1:]] - flat_phi[rows, xj[1:]]
    i_d, i_j = index[rows, xd[:-1]], index[rows, xj[:-1]]
    out["runs"] = {"x_d": xd.tolist(), "x_j": xj.tolist(), "e": e.tolist(), "I_d": i_d.tolist(), "I_j": i_j.tolist()}
    start_reach = bool(np.abs(np.array(inst.x0) - mins[0]).max() <= r)
    out["start_in_reach"] = start_reach
    valid = out["unique_minimisers"] and out["a"]
    out["target"] = {}
    for metric in METRICS:
        if not (valid and cond_c[metric]):
            continue
        alpha, beta = slopes[metric]
        const = 1.0 + beta / alpha
        out["target"][metric] = {"C": const, "D": _prefix_check(e, i_d, const, tol),
                                 "J": _prefix_check(e, i_j, const, tol)}
    out["t4a"] = {}
    if valid and start_reach:
        for metric in METRICS:
            alpha, beta = slopes[metric]
            ok1 = e[0] <= i_d[0] + tol
            ok2 = t_max < 2 or e[0] + e[1] <= (1.0 + beta / alpha) * i_d[0] + i_d[1] + tol
            out["t4a"][metric] = bool(ok1 and ok2)
    out["t4b"] = {}
    if valid and start_reach:  # e_0 <= I_0, e_{t+1} <= (beta/alpha) e_t + I_{t+1}, and the sum
        for metric in METRICS:
            alpha, beta = slopes[metric]
            q = beta / alpha
            rec = bool(e[0] <= i_d[0] + tol and all(e[t + 1] <= q * e[t] + i_d[t + 1] + tol * (1.0 + q)
                                                      for t in range(t_max - 1)))
            lag = np.subtract.outer(np.arange(t_max), np.arange(t_max))  # (T' - 1) - s
            geo = np.cumsum(q ** np.arange(t_max, dtype=float))  # geo[j] = sum_{u<=j} q^u
            coef = np.where(lag >= 0, geo[np.clip(lag, 0, None)], 0.0)
            cum = bool((np.cumsum(e) <= coef @ i_d + tol * (1.0 + q) ** t_max).all())
            out["t4b"][metric] = rec and cum
    reach = dist(z_now, "linf") <= r
    out["max_index_in_reach"] = float(index[reach].max())
    alpha_inf = slopes["linf"][0]
    cond_e = out["max_index_in_reach"] <= alpha_inf * (r - out["rho"]) + tol
    out["within_assumptions"] = bool(valid and start_reach and cond_e)
    if valid and start_reach:
        dd = np.abs(coords[xd] - np.vstack([mins, mins[-1:]])).max(axis=1)[:-1]
        dj = np.abs(coords[xj] - np.vstack([mins, mins[-1:]])).max(axis=1)[:-1]
        out["within"] = {
            "cond_e": bool(cond_e),
            "invariant": bool((dd <= r).all() and (dj <= r).all()),
            "joint_on_minimiser": bool((xj[1:] == m_flat).all()),
            "excess_nonneg": bool((e >= -tol).all()),
            "excess_le_index": bool((np.cumsum(e) <= np.cumsum(i_d) + tol).all()),
            "step_le_index": bool((e <= i_d + tol).all()),
        }
    return out


# ---------------------------------------------------------------- chunks

def run_target(start: int, draws: int) -> dict:
    """Draw ids [start, start + draws); keep the instances that satisfy (a),
    unique minimisers and (c) for at least one metric."""
    c, by_shape, by_path, by_start = Counter(), Counter(), Counter(), Counter()
    viol_shape, viol_path, viol_start = Counter(), Counter(), Counter()
    smallest, worst = [], {}
    t0 = time.perf_counter()
    for i in range(start, start + draws):
        inst = draw_instance(i, "target")
        ev = evaluate(inst)
        c["drawn"] += 1
        if not ev["unique_minimisers"]:
            c["rejected_minimiser"] += 1
            continue
        if not ev["target"]:
            c["rejected_c"] += 1
            if ev["t4a"] and not all(ev["t4a"].values()):
                c["t4a_violation_rejected_set"] += 1
            continue
        fam = ev["family"]
        c["accepted"] += 1
        by_shape[fam["shape"]] += 1
        by_path[fam["path"]] += 1
        by_start[fam["start"]] += 1
        any_viol = False
        for metric, res in ev["target"].items():
            c[f"accepted_{metric}"] += 1
            for choice in ("D", "J"):
                chk = res[choice]
                key = f"{metric}_{choice}"
                worst[key] = max(worst.get(key, 0.0), chk["max_ratio"])
                if chk["violated"]:
                    c[f"violated_{key}"] += 1
                    if choice == "D":
                        any_viol = True
                        if fam["start"] == "reach":
                            c[f"violated_{key}_reach"] += 1
                            worst[f"first_{key}_reach"] = min(worst.get(f"first_{key}_reach", 99), chk["first"])
                        smallest.append((chk["first"], ev["K"], ev["N"], ev["r"], i, metric, fam["start"],
                                         round(chk["max_ratio"], 4)))
        if any_viol:
            c["violated_any_D"] += 1
            viol_shape[fam["shape"]] += 1
            viol_path[fam["path"]] += 1
            viol_start[fam["start"]] += 1
        for metric, ok in ev["t4a"].items():
            c[f"t4a_checked_{metric}"] += 1
            c[f"t4a_violation_{metric}"] += not ok
        for metric, ok in ev["t4b"].items():
            c[f"t4b_checked_{metric}"] += 1
            c[f"t4b_violation_{metric}"] += not ok
        if ev.get("within_assumptions"):
            c["within_assumptions"] += 1
            w = ev["within"]
            c["within_failed"] += not (w["invariant"] and w["joint_on_minimiser"] and w["excess_nonneg"]
                                       and w["excess_le_index"])
    smallest.sort()
    return {"start": start, "draws": draws, "seconds": round(time.perf_counter() - t0, 1),
            "counts": dict(c), "by_shape": dict(by_shape), "by_path": dict(by_path), "by_start": dict(by_start),
            "violations_by_shape": dict(viol_shape), "violations_by_path": dict(viol_path),
            "violations_by_start": dict(viol_start), "worst": worst, "smallest": smallest[:MAX_LISTED]}


def run_within(start: int, draws: int) -> dict:
    """Draw ids [start, start + draws) of the slow-minimiser generator; keep
    the instances that satisfy T4''s assumptions; also count what happens on
    instances that fail only the index condition (e)."""
    c, worst = Counter(), {}
    t0 = time.perf_counter()
    for i in range(start, start + draws):
        ev = evaluate(draw_instance(i, "within"))
        c["drawn"] += 1
        if not (ev["unique_minimisers"] and ev["a"] and ev["start_in_reach"]):
            c["rejected_other"] += 1
            continue
        for metric, ok in ev["t4a"].items():  # T4a and T4b need only (a), (b) and the start
            c[f"t4a_checked_{metric}"] += 1
            c[f"t4a_violation_{metric}"] += not ok
        for metric, ok in ev["t4b"].items():
            c[f"t4b_checked_{metric}"] += 1
            c[f"t4b_violation_{metric}"] += not ok
        w = ev["within"]
        if not w["cond_e"]:
            c["rejected_e"] += 1
            c["e_fails_invariant_breaks"] += not w["invariant"]
            c["e_fails_excess_above_index"] += not w["excess_le_index"]
            continue
        c["accepted"] += 1
        c["nonzero_index"] += float(np.sum(ev["runs"]["I_d"])) > 0.0
        c["positive_excess"] += float(np.sum(ev["runs"]["e"])) > 1e-9
        for name in ("invariant", "joint_on_minimiser", "excess_nonneg", "excess_le_index", "step_le_index"):
            c[f"failed_{name}"] += not w[name]
        e_sum, i_sum = float(np.sum(ev["runs"]["e"])), float(np.sum(ev["runs"]["I_d"]))
        if i_sum > 0:
            worst["max_E_over_sumI"] = max(worst.get("max_E_over_sumI", 0.0), e_sum / i_sum)
        worst["max_T"] = max(worst.get("max_T", 0), ev["T"])
        c[f"K{ev['K']}_r{ev['r']}"] += 1
    return {"start": start, "draws": draws, "seconds": round(time.perf_counter() - t0, 1),
            "counts": dict(c), "worst": worst}


def drift_instance(t_max: int, lam: float = 1.01, w: float = 1.0) -> Instance:
    """The explicit family: K = 2, r = 1, N = T + 3. Step 0 has a valley
    interaction lam |z_1 - z_2| that keeps the per-knob run in place while
    the joint run moves onto m_0; afterwards the objective is additive
    (index 0) and its minimiser drifts diagonally at full speed, away from
    the per-knob run, which therefore stays one diagonal step behind."""
    n = t_max + 3
    coords = grid_coords(2, n).reshape(n, n, 2)
    phis, mins = [], []
    for t in range(t_max):
        m = (t_max - t, t_max - t)
        z = (coords - np.array(m)).astype(float)
        phi_t = w * np.abs(z).sum(axis=-1) + (lam * np.abs(z[..., 0] - z[..., 1]) if t == 0 else 0.0)
        phis.append(phi_t)
        mins.append(m)
    family = {"K": 2, "N": n, "r": 1, "T": t_max, "shape": "valley at t = 0", "path": "diagonal drift",
              "start": "reach", "lambda": lam}
    return Instance(-t_max, "drift", family, 1, np.array(phis), np.array(mins), (t_max + 1, t_max + 1))


def run_drift(t_values=range(2, 25)) -> dict:
    rows = []
    for t_max in t_values:
        ev = evaluate(drift_instance(t_max))
        res = ev["target"].get("linf")
        rows.append({"T": t_max, "valid": bool(ev["unique_minimisers"] and ev["a"]),
                     "c_linf": ev["c"]["linf"], "c_l1": ev["c"]["l1"],
                     "alpha": ev["alpha"]["linf"], "beta": ev["beta"]["linf"],
                     "C": None if res is None else res["C"],
                     "E": float(np.sum(ev["runs"]["e"])), "sum_I": float(np.sum(ev["runs"]["I_d"])),
                     "violated": None if res is None else res["D"]["violated"]})
    return {"rows": rows}


def search_minimal(start: str, t_values=(2, 3, 4), n_values=range(3, 9), draws_per_cell: int = 4000) -> dict:
    """Smallest horizon, then smallest grid, at which a counterexample to the
    conjectured constant appears from the given start condition: K = 2,
    r = 1, integer weights; draws per (T, N) cell are capped, and the cells
    searched without success are recorded."""
    empty = []
    for t_max in t_values:
        for n in n_values:
            hit = _search_cell(start, t_max, n, draws_per_cell)
            if hit is not None:
                return {"hit": hit, "empty_cells": empty, "draws_per_cell": draws_per_cell}
            empty.append([t_max, n])
    return {"hit": None, "empty_cells": empty, "draws_per_cell": draws_per_cell}


def _search_cell(start: str, t_max: int, n: int, draws: int) -> dict | None:
    base = (t_max * 100 + n) * 100_000 + (0 if start == "reach" else 50_000)
    for j in range(draws):
        inst = draw_instance(base + j, "minimal",
                             {"K": 2, "r": 1, "N": n, "T": t_max, "start": start, "numbers": "int"})
        ev = evaluate(inst)
        for metric, res in ev["target"].items():
            if res["D"]["violated"] and res["D"]["first"] == t_max:
                return describe_counterexample(inst, ev, metric)
    return None


def describe_counterexample(inst: Instance, ev: dict, metric: str) -> dict:
    t_max, n_knobs, n = inst.shape
    coords = grid_coords(n_knobs, n)
    return {"id": inst.inst_id, "kind": inst.kind, "metric": metric, "family": inst.family,
            "T": t_max, "N": n, "r": inst.r, "x0": list(inst.x0),
            "minimisers": inst.minimisers.tolist(),
            "phi": [np.round(p, 6).tolist() for p in inst.phi],
            "x_d": [coords[i].tolist() for i in ev["runs"]["x_d"]],
            "x_j": [coords[i].tolist() for i in ev["runs"]["x_j"]],
            "e": ev["runs"]["e"], "I_d": ev["runs"]["I_d"], "I_j": ev["runs"]["I_j"],
            "alpha": ev["alpha"][metric], "beta": ev["beta"][metric], "C": ev["target"][metric]["C"],
            "E": ev["target"][metric]["D"]["E"], "sum_I": ev["target"][metric]["D"]["S"]}


# ---------------------------------------------------------------- report

def _sum_chunks(chunks: list[dict]) -> tuple[Counter, dict, dict]:
    tot, worst, dicts = Counter(), {}, {}
    for ch in chunks:
        tot += Counter(ch["counts"])
        for k, v in ch.get("worst", {}).items():
            worst[k] = min(worst.get(k, v), v) if k.startswith("first_") else max(worst.get(k, v), v)
        for name in ("by_shape", "by_path", "by_start", "violations_by_shape", "violations_by_path",
                     "violations_by_start"):
            dicts.setdefault(name, Counter()).update(ch.get(name, {}))
    return tot, worst, dicts


def _fmt_table(tables: list[list]) -> str:
    return "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in tables)


def report_section(data: dict) -> list[str]:
    """The T4 part of PROPERTY_CHECKS.md."""
    lines = ["## T4: a run-level bound (WP18, stretch)", ""]
    target = sorted(data.get("target", {}).values(), key=lambda ch: ch["start"])
    if not target:
        return lines + ["Not run yet.", ""]
    tot, worst, dicts = _sum_chunks(target)
    smallest = sorted(tuple(s) for ch in target for s in ch["smallest"])[:10]
    lines += [
        "Formal versions used (also in `INDEX_THEORY.md`, section T4, and the docstring of `t4_check.py`): "
        "grid {0..N-1}^K, box B(x) = points within l_inf distance r of x, objectives phi_t with a unique "
        "minimiser m_t; joint and per-knob greedy steps as in `interaction_index`; I_t = index of phi_t on "
        "the box of the per-knob run's state (and, separately, of the joint run's state). (a) "
        "|m_{t+1} - m_t|_inf <= r. (b) alpha and beta are the tightest growth and per-step slopes in the "
        "metric d (l_inf or l_1). (c) for every t and every state x, neither greedy step increases d(x, m_t). "
        "Conjecture: E_T' <= (1 + beta/alpha) sum_{t<T'} I_t for every prefix T'.",
        "",
        f"Instances drawn: {tot['drawn']:,} (draw ids {target[0]['start']:,} to "
        f"{target[-1]['start'] + target[-1]['draws'] - 1:,}); rejected for a tied minimiser: "
        f"{tot['rejected_minimiser']:,}; rejected because (c) failed in both metrics: {tot['rejected_c']:,}; "
        f"**accepted: {tot['accepted']:,}** ({tot['accepted_linf']:,} satisfy (c) in l_inf, "
        f"{tot['accepted_l1']:,} in l_1). K = 2 (N 4-12, r 1-2, T 2-16) or K = 3 (N 4-7, r = 1, T 2-12); "
        "objective sum_k w_k |z_k| + lambda_t psi(z) with z = x - m_t and psi one of max, l2, valley "
        "|z_i - z_j|, ridge |z_i + z_j|, their negatives (lambda < min w), a random field, or none; "
        "lambda_t on always, for a first stretch, at random or at one step; minimiser path a random walk, "
        "a full-speed drift, still, or adversarial (moving away from the per-knob run); start within reach "
        "of m_0 or anywhere; weights real or integer, static or varying.",
        "",
        "| metric, index along | instances checked | violations | of which start within reach | "
        "largest E / (C sum I) |",
        "|---|---:|---:|---:|---:|",
    ]
    for metric in METRICS:
        for choice, name in (("D", "per-knob run"), ("J", "joint run")):
            key = f"{metric}_{choice}"
            reach = tot[f"violated_{key}_reach"] if choice == "D" else "-"
            lines.append(f"| {metric}, {name} | {tot[f'accepted_{metric}']:,} | {tot[f'violated_{key}']:,} | "
                         f"{reach if isinstance(reach, str) else format(reach, ',')} | {worst.get(key, 0.0):.3g} |")
    vs, vp, vst = dicts["violations_by_shape"], dicts["violations_by_path"], dicts["violations_by_start"]
    bs, bp, bst = dicts["by_shape"], dicts["by_path"], dicts["by_start"]
    lines += [
        "",
        f"**Result: the conjecture fails.** {tot['violated_any_D']:,} of {tot['accepted']:,} accepted instances "
        f"({100.0 * tot['violated_any_D'] / max(1, tot['accepted']):.1f}%) violate it in at least one metric "
        "with the index along the per-knob run. Violations by minimiser path: "
        + ", ".join(f"{k} {vp.get(k, 0):,} of {bp.get(k, 0):,}" for k in PATHS)
        + "; by start: " + ", ".join(f"{k} {vst.get(k, 0):,} of {bst.get(k, 0):,}" for k in ("reach", "anywhere"))
        + "; by shape: " + ", ".join(f"{k} {vs.get(k, 0):,}/{bs.get(k, 0):,}" for k in SHAPES) + ".",
        "",
        f"Earliest violating prefix with a start within reach: T' = {worst.get('first_linf_D_reach', 'none')} "
        f"(l_inf), {worst.get('first_l1_D_reach', 'none')} (l_1). The two-step proposition T4a "
        f"(E_1 <= I_0 and E_2 <= (1 + beta/alpha) I_0 + I_1 from a start within reach) was checked on "
        f"{tot['t4a_checked_linf']:,} (l_inf) and {tot['t4a_checked_l1']:,} (l_1) accepted instances: violations "
        f"{tot['t4a_violation_linf']:,} and {tot['t4a_violation_l1']:,} (and {tot['t4a_violation_rejected_set']:,} "
        "among instances rejected for (c), which T4a does not need).",
        "",
        "Smallest violations among the accepted instances (prefix T', K, N, r, draw id, metric, start, "
        "E / (C sum I)): " + "; ".join(str(s) for s in smallest) + ".",
        "",
    ]
    lines += _drift_lines(data.get("drift"))
    lines += _minimal_lines(data.get("minimal"))
    lines += _within_lines(sorted(data.get("within", {}).values(), key=lambda ch: ch["start"]))
    return lines


def _drift_lines(drift: dict | None) -> list[str]:
    if not drift:
        return []
    rows = drift["rows"]
    pick = [row for row in rows if row["T"] in (2, 3, 4, 6, 8, 12, 16, 24)]
    lines = [
        "### The mechanism, as an explicit family",
        "",
        "`t4_check.drift_instance(T)`: K = 2, r = 1, N = T + 3, start one diagonal step from m_0. At step 0, "
        "phi_0 = |z_1| + |z_2| + 1.01 |z_1 - z_2|: per-knob search cannot leave the valley, the joint step "
        "lands on m_0. From step 1 on, phi_t = |z_1| + |z_2| (additive, index 0) and m_t moves one step "
        "diagonally per step, away from the per-knob run. (a), (b) and (c) hold in both metrics "
        "(checked by `evaluate` on the whole grid). The per-knob run stays one diagonal step behind and "
        "pays 2 at every step; the summed index stays 2.02.",
        "",
        "| T | (c) l_inf | C = 1 + beta/alpha (l_inf) | E_T | sum I_t | E_T / sum I_t | violates |",
        "|---:|:---:|---:|---:|---:|---:|:---:|",
    ]
    for row in pick:
        ratio = row["E"] / row["sum_I"] if row["sum_I"] else math.inf
        c = "-" if row["C"] is None else f"{row['C']:.2f}"
        lines.append(f"| {row['T']} | {row['c_linf']} | {c} | {row['E']:.2f} | {row['sum_I']:.2f} | "
                     f"{ratio:.2f} | {row['violated']} |")
    lines += ["", "E_T / sum I_t grows linearly in T while the constant stays fixed: under (a)-(c) no "
              "constant whatsoever bounds the run-level excess by the summed index.", ""]
    return lines


def _minimal_lines(minimal: dict | None) -> list[str]:
    if not minimal:
        return []
    lines = ["### Smallest counterexamples found", ""]
    for start, res in minimal.items():
        hit = res["hit"]
        empty = ", ".join(f"(T={t}, N={n})" for t, n in res["empty_cells"])
        lines += [f"Start {start}: cells searched without a violation ({res['draws_per_cell']:,} draws each): "
                  f"{empty or 'none'}.", ""]
        if not hit:
            continue
        lines += [
            f"**Start {start}** (search order: horizon T, then grid size N; K = 2, r = 1, integer weights; "
            f"draw id {hit['id']}, metric {hit['metric']}): T = {hit['T']}, N = {hit['N']}, "
            f"family {hit['family']['shape']}/{hit['family']['path']}/{hit['family']['schedule']}, "
            f"lambda = {hit['family']['lambda']:.4g}. alpha = {hit['alpha']:.4g}, beta = {hit['beta']:.4g}, "
            f"C = {hit['C']:.4g}; E_T = {hit['E']:.4g} > C x sum I = {hit['C'] * hit['sum_I']:.4g} "
            f"(sum I = {hit['sum_I']:.4g}).",
            "",
            f"x_0 = {hit['x0']}; minimisers m_t = {hit['minimisers']}; per-knob run {hit['x_d']}; joint run "
            f"{hit['x_j']}; step excesses e_t = {[round(v, 4) for v in hit['e']]}; I_t along the per-knob run "
            f"= {[round(v, 4) for v in hit['I_d']]}.",
            "",
        ]
        for t, table in enumerate(hit["phi"]):
            lines += [f"phi_{t} (rows: knob 1 = 0..{hit['N'] - 1}; columns: knob 2):", "", "```"]
            lines += ["  ".join(f"{v:7.3f}" for v in row) for row in table]
            lines += ["```", ""]
    return lines


def _within_lines(chunks: list[dict]) -> list[str]:
    if not chunks:
        return ["### T4' (within reach)", "", "Not run yet.", ""]
    tot, worst, _ = _sum_chunks(chunks)
    failed = sum(tot[f"failed_{k}"] for k in ("invariant", "joint_on_minimiser", "excess_nonneg",
                                               "excess_le_index", "step_le_index"))
    return [
        "### T4' (within reach): the claim after changing the assumptions",
        "",
        "Assumptions: start within reach (|x_0 - m_0|_inf <= r); the minimiser moves at most rho <= r per "
        "step; unique minimisers with l_inf growth slope alpha; and (e) I_t(x) <= alpha (r - rho) at every "
        "state x within reach of m_t. No Lipschitz bound and no (c) are needed. Claim: both runs stay "
        "within reach, the joint run lands on m_t at every step, and 0 <= e_t <= I_t(x^D_t), hence "
        "0 <= E_T' <= sum_{t<T'} I_t (constant 1).",
        "",
        f"Instances drawn: {tot['drawn']:,} (slow minimiser: rho <= r - 1; r 1-3 for K = 2, 1-2 for K = 3; "
        f"lambda scaled to the slack); rejected for (e): {tot['rejected_e']:,}; other rejections "
        f"{tot['rejected_other']:,}; **accepted: {tot['accepted']:,}**, of which {tot['nonzero_index']:,} have a "
        f"positive summed index and {tot['positive_excess']:,} a positive excess. Failures of the claim "
        f"(invariant, joint on the minimiser, sign, per-step and cumulative bound): **{failed}**. Largest "
        f"E / sum I: {worst.get('max_E_over_sumI', 0.0):.4f}.",
        "",
        f"Negative control: on the {tot['rejected_e']:,} instances that fail only (e), the within-reach "
        f"invariant broke in {tot['e_fails_invariant_breaks']:,} and E > sum I in "
        f"{tot['e_fails_excess_above_index']:,}.",
        "",
        f"T4a was also checked on every valid draw of this generator (start within reach, (a) holds): "
        f"{tot['t4a_checked_linf']:,} (l_inf) and {tot['t4a_checked_l1']:,} (l_1) instances, violations "
        f"{tot['t4a_violation_linf']:,} and {tot['t4a_violation_l1']:,}.",
        "",
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("target", "within"):
        s = sub.add_parser(name)
        s.add_argument("--start", type=int, default=0)
        s.add_argument("--draws", type=int, default=40_000)
    m = sub.add_parser("minimal")
    m.add_argument("--start-kind", choices=("reach", "anywhere"), required=True)
    m.add_argument("--draws-per-cell", type=int, default=4000)
    sub.add_parser("drift")
    args = ap.parse_args(argv)
    # compute first; read, merge and write the results file only at the end
    if args.cmd == "target":
        ch = run_target(args.start, args.draws)
        update = ("target", str(args.start), ch)
        print(f"target {args.start}..: {ch['seconds']} s, accepted {ch['counts'].get('accepted', 0)}, "
              f"violated (per-knob index, any metric) {ch['counts'].get('violated_any_D', 0)}")
    elif args.cmd == "within":
        ch = run_within(args.start, args.draws)
        update = ("within", str(args.start), ch)
        print(f"within {args.start}..: {ch['seconds']} s, accepted {ch['counts'].get('accepted', 0)}, "
              f"counts {ch['counts']}")
    elif args.cmd == "minimal":
        res = search_minimal(args.start_kind, draws_per_cell=args.draws_per_cell)
        update = ("minimal", args.start_kind, res)
        hit = res["hit"]
        print(args.start_kind, "empty cells", res["empty_cells"],
              "hit", None if hit is None else (hit["T"], hit["N"], hit["metric"], hit["id"]))
    else:
        res = run_drift()
        update = ("drift", None, res)
        print([(r["T"], round(r["E"] / r["sum_I"], 2) if r["sum_I"] else None, r["violated"])
               for r in res["rows"]])
    data = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    section, key, value = update
    if key is None:
        data[section] = value
    else:
        data.setdefault(section, {})[key] = value
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
