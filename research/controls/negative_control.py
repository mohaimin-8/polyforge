"""WP22 item 1: the negative control (programme 2).

Claim under test. When the planner's objective is exactly additive across a
tenant's knobs, phi(r, c, t) = F(r) + G(c) + H(t), the remainder R is zero on
the whole box, so the interaction index is zero, and per-knob search and
joint search choose the same point. Any difference is a bug -- unless the
feasible set is not a product of per-knob sets or the anchor itself is
infeasible: those two are the only ways an additive objective can still
separate the searches, and both lie outside Theorem 1's hypotheses
(`unit_exceptions` constructs each).

What makes the simulator's objective non-additive (see README.md for the
table with line numbers): tier spend is AI misses x tier price (cache x
tier); AI miss latency is the tier's latency times the tenant's replica
congestion factor (replicas x tier); the AI latency mixes cache hits with
misses (cache x tier); AI work loads the replicas through the cache's hit
rate (replicas x cache x tier); the dollar admission gate refuses spend
beyond the budget left after infrastructure (all three); the planner's
log1p(excess) and Jain fairness are nonlinear in the SUM of the traffic
families' terms; the budget filter bounds a sum of per-knob spends. A
consequence: in this simulator an exactly additive configuration has at most
ONE knob with a service-level effect; the other two may enter only through
linear infrastructure cost and the switching penalty.

The controls below therefore pick, per configuration, the one knob that
carries every service-level effect, and remove each coupling by a stated
setting:

  crud_only        published plant and planner, CRUD-only workloads: replicas
  crud_active      crud_only on the overload classes, half-replica switching
                   penalty (replicas follow the bursts): replicas
  shared_capacity  published plant, AI work on the replicas (kappa 2, width 8),
                   no cache hits, one latency for every real tier, no tier
                   "none", no budget filter: replicas
  mode_a_tier      plant 2 mode A, chat-only population workloads, no cache
                   hits, no admission gate, no budget filter, answer quality
                   priced (weight 2): the tier
  mode_a_tier_free mode_a_tier with no switching penalty, so all three knobs move
  mode_a_cache     mode A, every real tier at the large tier's latency and
                   price, no tier "none", no gate, no budget filter: the cache

and, as a sensitivity check that the instrument is not trivially zero, mode
A as published and mode A without its gate (both must show a positive index).

Checks per run (joint arm logs the index of every decision and counts, at
every best-response call, whether the decoupled search would have chosen
differently from identical inputs; the decoupled arm is run separately):
index <= tolerance on every decision where it is defined; per-knob choice ==
joint choice on every decision; zero planner-level disagreements; the two
arms' outcome metrics bit-identical.

    python negative_control.py --smoke            # one short cell per configuration (about 70 s)
    python negative_control.py --workers 6        # the full set (about 35 CPU-minutes)
    python negative_control.py --smoke --report-only   # re-render a report from its saved runs
"""

from __future__ import annotations

import argparse
import itertools
import math
import random
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache
from multiprocessing import Pool

import controls_common as cc
from interaction_index import analyse_box

import pandas as pd

# Remainders are differences of floating-point sums of a few terms of size
# |phi|; their rounding error is a few ulps of |phi|. 1e-12 relative is two
# orders above that and many orders below any interaction the pilots measured.
TOL_REL = 1e-12
SALT = "wp22_negative"
CRUD_WORKLOADS = ("crud_bursty", "crud_steady", "mmpp_crud")
SHARED_WORKLOADS = ("ai_cacheable", "ai_uncacheable", "agentic", "joint_stress")
MIXES = ("uniform", "premium_heavy", "besteffort_heavy", "whale")


# ---------------------------------------------------------------- unit level

def separable_objective(mains: list[list[float]]):
    """phi(x) = sum_k mains[k][x_k]: additive by construction."""
    return lambda x: sum(m[v] for m, v in zip(mains, x))


def unit_control(n_boxes: int = 2000, seed: int = 20261112) -> dict:
    """Random separable objectives on random boxes, some knob values
    infeasible on every point (a product-form feasible set) and the anchor
    feasible: the index is zero, the loss is zero and x^D == x^J."""
    rng = random.Random(seed)
    worst_index = worst_loss = 0.0
    disagreements = checked = 0
    for _ in range(n_boxes):
        k = rng.randint(2, 4)
        values = [list(range(rng.randint(1, 6))) for _ in range(k)]
        mains = [[rng.gauss(0.0, 1.0) for _ in v] for v in values]
        anchor = tuple(rng.choice(v) for v in values)
        # product-form infeasibility: drop whole values of a knob, never the anchor's
        banned = [{v for v in vals if v != a and rng.random() < 0.2} for vals, a in zip(values, anchor)]
        base = separable_objective(mains)

        def phi(x, base=base, banned=banned):
            return None if any(v in b for v, b in zip(x, banned)) else base(x)

        a = analyse_box(values, phi, anchor)
        checked += 1
        worst_index = max(worst_index, a.oscillation)
        worst_loss = max(worst_loss, abs(a.loss))
        disagreements += a.per_knob != a.joint
    return {"boxes": checked, "worst_index": worst_index, "worst_loss": worst_loss,
            "disagreements": disagreements}


def unit_exceptions() -> dict:
    """The two ways an ADDITIVE objective still separates per-knob from
    joint search, both outside Theorem 1's hypotheses:

    budget: phi = spend_1 + spend_2 - benefit, feasible only when
    spend_1 + spend_2 <= budget (a sum, not a product of per-knob sets).
    Each knob alone may raise its spend; together they break the budget, so
    x^D is infeasible while R is zero wherever it is defined.

    anchor: the anchor itself infeasible (a product-form constraint the
    others' plan made binding). Every axis through it is infeasible for the
    other knobs, so they hold; R is undefined everywhere (no index)."""
    values = [[0, 1], [0, 1]]
    spend = {0: 0.0, 1: 1.0}

    def phi_budget(x):
        if spend[x[0]] + spend[x[1]] > 1.0:
            return None
        return -1.5 * x[0] - 1.0 * x[1]

    budget = analyse_box(values, phi_budget, (0, 0))

    def phi_anchor(x):
        if x[0] == 0:  # knob 0's anchor value is infeasible (a binding cap)
            return None
        return -1.0 * x[1]

    anchor = analyse_box(values, phi_anchor, (0, 0))
    return {"budget": budget, "anchor": anchor}


# ----------------------------------------------------------- configurations

@dataclass(frozen=True)
class Control:
    name: str
    carrier: str  # the one knob with service-level effects
    why: str
    cells: tuple  # (workload, mix, size) design points of the full run
    economy: dict = field(default_factory=dict)
    model_form: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    tier_min: str | None = None
    expect_zero: bool = True  # False: a sensitivity row, the index must be positive


def _mode_a(**overrides) -> tuple[dict, dict]:
    import plant

    return plant.mode_a(**overrides)


def _population_cells(n: int, size: str = "large") -> tuple:
    from harness import population_workloads

    return tuple((w, "own", size) for w in population_workloads.names("tuning")[:n])


@lru_cache(maxsize=None)
def controls(pop_workloads: int = 6) -> tuple[Control, ...]:
    """The configurations. Drivers that keep additivity but make the carrier
    knob MOVE (a control in which nothing moves cannot catch a search bug):
    the half-replica switching penalty of the corrected arms (the published
    0.05 exceeds a replica-step's saving, so replicas ratchet and rarely
    move), the registered QUALITY weight 2.0 (with no cache hits the quality
    loss depends on the tier only), and, for the cache carrier, every real
    tier at the large tier's latency and price (hits then save real money
    and real latency)."""
    from controller import SWITCH_PENALTY_HALF_REPLICA

    pop = _population_cells(pop_workloads)
    half = {"switch_penalty": SWITCH_PENALTY_HALF_REPLICA}
    tier_form, tier_econ = _mode_a(cache_hit_max=0.0, admission_gate=0)
    cache_form, cache_econ = _mode_a(admission_gate=0)
    lat, price = cache_form["tier_latency_large"], cache_econ["tier_cost_large"]
    cache_form = {**cache_form, "tier_latency_small": lat, "tier_latency_mid": lat}
    cache_econ = {**cache_econ, "tier_cost_small": price, "tier_cost_mid": price}
    asis_form, asis_econ = _mode_a()
    nogate_form, nogate_econ = _mode_a(admission_gate=0)
    no_budget = {"enforce_budget": False}
    quality = {"quality_weight": 2.0}
    return (
        Control("crud_only", "replicas",
                "published plant and planner, no AI request: tier enters nothing, cache only its "
                "memory price (the cells of the committed JOINTNESS check)",
                tuple(itertools.product(CRUD_WORKLOADS, MIXES, ("small", "medium", "large")))),
        Control("crud_active", "replicas",
                "crud_only on the overload classes with the half-replica switching penalty, so "
                "replicas follow the bursts up and down",
                tuple(itertools.product(("flash_crud", "ramp_gentle", "crud_bursty", "mmpp_crud"),
                                        ("uniform", "whale"), ("small", "large"))),
                params=half),
        Control("shared_capacity", "replicas",
                "AI work on the replicas (kappa 2, width 8) but no cache hits and one latency for "
                "every real tier: congestion depends on replicas only; tier enters by price only",
                tuple((w, m, "large") for w in SHARED_WORKLOADS for m in ("uniform", "whale")),
                economy={"cache_hit_max": 0.0},
                model_form={"wu_ai_scale": 2.0, "tier_latency_small": 800.0,
                            "tier_latency_mid": 800.0, "tier_latency_large": 800.0},
                params={**no_budget, **half, **cc.width_params(8)}, tier_min="small"),
        Control("mode_a_tier", "tier",
                "mode A, chat only: replicas carry no load (congestion 1), no cache hits, no gate, "
                "answer quality priced: replicas and cache enter by price only",
                pop, economy=tier_econ, model_form=tier_form, params={**no_budget, **quality}),
        Control("mode_a_tier_free", "tier", "mode_a_tier with no switching penalty: every knob moves",
                pop, economy=tier_econ, model_form=tier_form,
                params={**no_budget, **quality, "switch_penalty": 0.0}),
        Control("mode_a_cache", "cache",
                "mode A, chat only, small/mid/large all at the large tier's latency and price, no "
                "tier 'none', no gate: replicas enter by price only, tier by the switching penalty",
                pop, economy=cache_econ, model_form=cache_form, params=no_budget, tier_min="small"),
        Control("mode_a_published", "-", "sensitivity: mode A as published (gate on, cache hits)",
                pop, economy=asis_econ, model_form=asis_form, expect_zero=False),
        Control("mode_a_no_gate", "-", "sensitivity: mode A without the admission gate",
                pop, economy=nogate_econ, model_form=nogate_form, params=no_budget, expect_zero=False),
    )


def control_by_name(name: str, pop_workloads: int = 6) -> Control:
    for c in controls(pop_workloads):
        if c.name == name:
            return c
    raise KeyError(name)


# ------------------------------------------------------------------ running

def _moved(rows: list[dict], k: int) -> float:
    rows = [r for r in rows if r["joint"] is not None]
    return float(sum(r["joint"][k] != r["anchor"][k] for r in rows) / len(rows)) if rows else math.nan


def run_cell(job: tuple) -> dict:
    """Both arms of one design point of one configuration; one summary row.
    `job` = (control name, workload, mix, size, steps, pop_workloads)."""
    name, workload, mix, size, steps, pop_n = job
    ctl = control_by_name(name, pop_n)
    seed = cc.design_seed(SALT, workload, mix, size, 0)
    runs = cc.run_specs(f"wp22_negative_{name}", workload, mix, size, steps, seed,
                        ctl.economy, ctl.model_form)
    arms = cc.arm_pair(ctl.params, tier_min=ctl.tier_min)
    started = time.time()
    out = cc.execute_pair(runs, arms)
    rows = [r for r in out["rows"] if r["cycle"] <= steps]
    mj, md = out["joint"]["metrics"], out["decoupled"]["metrics"]
    nonzero = [r for r in rows if r["index"] is not None
               and r["index"] > TOL_REL * max(1.0, abs(r["phi_joint"] or 0.0))]
    box_disagree = [r for r in rows if r["joint"] is not None and r["per_knob"] != r["joint"]]
    first = (nonzero or box_disagree or [None])[0]
    diffs = {k: abs(mj[k] - md[k]) for k in cc.OUTCOME_METRICS
             if mj[k] is not None and md[k] is not None}
    rel = [r["index"] / max(1.0, abs(r["phi_joint"] or 0.0)) for r in rows if r["index"] is not None]
    return {
        "control": name, "carrier": ctl.carrier, "expect_zero": ctl.expect_zero,
        "workload": workload, "tenant_mix": mix, "cluster_size": size, "steps": steps, "seed": seed,
        "wall_s": time.time() - started,
        **cc.summarise_rows(rows, steps),
        "index_max_rel": max(rel) if rel else math.nan,
        "index_nonzero": len(nonzero),
        "box_disagreements": len(box_disagree),
        "planner_disagreement_share": mj["coordination_disagreement_share"],
        "identical": all(mj[k] == md[k] for k in cc.OUTCOME_METRICS),
        "max_metric_diff": max(diffs.values(), default=0.0),
        "J_joint": cc.objective_j(mj, out["joint"]["tenants"]),
        "J_decoupled": cc.objective_j(md, out["decoupled"]["tenants"]),
        "moved_replicas": _moved(rows, 0), "moved_cache": _moved(rows, 1), "moved_tier": _moved(rows, 2),
        "first_offender": None if first is None else
        f"cycle {first['cycle']} tenant {first['tenant']} anchor {first['anchor']} "
        f"x^D {first['per_knob']} x^J {first['joint']} index {first['index']}",
    }


def cell_passes(row: dict) -> bool:
    if not row["expect_zero"]:
        return row["index_mean"] > 1e-6  # the instrument sees the couplings
    return (row["index_nonzero"] == 0 and row["box_disagreements"] == 0
            and row["planner_disagreement_share"] == 0.0 and row["identical"])


# --------------------------------------------- binding caps (direct planner)

def _recording_controller(symmetric_gate: bool = False):
    from controller import JCACController

    class Recording(JCACController):
        """track_coordination that records every disagreement between the
        decoupled and the joint best response from identical inputs, with
        what explains it. Reporting only: the joint pick is returned.

        `symmetric_gate` (diagnosis only, never an arm): the decoupled
        search applies its keep-the-incumbent gate only when the incumbent
        is a member of the anchored lattice, as `_best_for_tenant` does."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.disagreements: list[dict] = []

        def _best_for_tenant_decoupled(self, tid, chosen, horizons, origin=None, cost_horizons=None):
            base = chosen[tid] if origin is None else origin[tid]
            if not (symmetric_gate and self.converge_sweeps
                    and chosen[tid] not in set(self._lattice(tid, base))):
                return super()._best_for_tenant_decoupled(tid, chosen, horizons, origin, cost_horizons)
            self.converge_sweeps = False  # this call only: no gate on an out-of-lattice incumbent
            try:
                return super()._best_for_tenant_decoupled(tid, chosen, horizons, origin, cost_horizons)
            finally:
                self.converge_sweeps = True

        def _best_for_tenant_tracked(self, tid, chosen, horizons, origin=None, cost_horizons=None):
            current = chosen[tid]
            dec = self._best_for_tenant_decoupled(tid, chosen, horizons, origin, cost_horizons)
            joint = self._best_for_tenant(tid, chosen, horizons, origin, cost_horizons)
            self.guard_counts["coord_calls"] += 1
            self.guard_counts["coord_disagreements"] += dec != joint
            if dec != joint:
                base = origin[tid]
                budget = self.configs[tid].hourly_budget_usd * 10 / 3600
                others = self._others(tid, chosen, horizons, cost_horizons)
                anchor = self._admitted_anchor(tid, base)
                anchor_ok = self._candidate_score(tid, anchor, base, others, horizons,
                                                  cost_horizons, budget) is not None
                in_lattice = current in set(self._lattice(tid, base))
                if not anchor_ok:
                    why = "anchor infeasible"
                elif dec == current and not in_lattice:
                    why = "decoupled gate kept an out-of-lattice incumbent"
                else:
                    why = "UNEXPLAINED"
                self.disagreements.append({"cycle": self._cycle, "tenant": tid, "base": base,
                                           "incumbent": current, "joint": joint, "decoupled": dec,
                                           "why": why})
            return joint

    return Recording


def cap_binding_probe(workload: str = "crud_steady", mix: str = "uniform", size: str = "small",
                      seed: int = 7, steps: int = 3, replica_cap: int = 12,
                      switch_penalty: float | None = 0.0, symmetric_gate: bool = False) -> dict:
    """CRUD-only demand (an exactly additive objective) under a cluster
    replica cap that binds from the first plan: 8 tenants start at 2
    replicas, 16 > 12. Every disagreement between the decoupled and the
    joint best response, from identical inputs, is classified:

    - "anchor infeasible": the interval-start state breaks the cap, so the
      per-knob axes through it are empty and those knobs hold (outside
      Theorem 1; the index is undefined there);
    - "decoupled gate kept an out-of-lattice incumbent": a tenant fell back
      earlier in the cycle to (replica_min, 0 MB, none), outside its anchored
      lattice; `_best_for_tenant` gates on its incumbent only when the
      incumbent is a lattice member (controller.py:1104-1121), while
      `_best_for_tenant_decoupled` scores and keeps ANY feasible incumbent
      (controller.py:1280-1284; knobwise likewise, 1330-1334). The decoupled
      arm then keeps a state the per-interval clamps forbid;
    - anything else is "UNEXPLAINED" (a bug to report).

    With `symmetric_gate` the second class must vanish (the diagnosis)."""
    from controller import ClusterLimits
    from harness import workloads
    from model import TenantState

    tids, buckets, configs, _ = workloads.build(workload, mix, size, seed, steps)
    params = {} if switch_penalty is None else {"switch_penalty": switch_penalty}
    ctl = _recording_controller(symmetric_gate)(
        configs, limits=ClusterLimits(cache_mb=8192, replicas=replica_cap), anchor_moves=True,
        converge_sweeps=True, track_coordination=True, **params)
    states = {t: TenantState() for t in tids}
    for demands in buckets:
        plans = ctl.plan(states, demands)
        states = {t: p.state for t, p in plans.items()}
    whys = [d["why"] for d in ctl.disagreements]
    return {"calls": ctl.guard_counts["coord_calls"], "disagreements": ctl.disagreements,
            "by_reason": {w: whys.count(w) for w in sorted(set(whys))}}


# ------------------------------------------------- committed campaign check

# metrics_shed_jointness.csv.gz is another export of the same experiment
# (name matrix_jointness, same seeds), so it is not counted twice.
COMMITTED = ("metrics_matrix_jointness",)
OUTCOME_COLUMNS = ("total_cost_usd", "mean_violation", "violation_step_share", "mean_jain",
                   "cache_hit_rate", "crud_p95_ms", "ai_p95_ms", "mean_excess", "tier_none_step_share")


def committed_check() -> pd.DataFrame:
    """Joint (jcac_converged) against decoupled (jcac_decoupled) in the
    committed exports that ran both with shared seeds: per workload, the
    number of design points and how many are bit-identical. No run needed."""
    out = []
    key = ["workload", "tenant_mix", "cluster_size", "rep"]
    for name in COMMITTED:
        path = cc.REPO / "eval" / "results" / f"{name}.csv.gz"
        df = pd.read_csv(path, float_precision="round_trip")
        cols = [c for c in OUTCOME_COLUMNS if c in df.columns]
        for wl, g in df.groupby("workload"):
            j = g[g.system == "jcac_converged"].set_index(key)[cols]
            d = g[g.system == "jcac_decoupled"].set_index(key)[cols].reindex(j.index)
            same = (j == d).all(axis=1)
            out.append({"export": name, "workload": wl, "pairs": len(j), "identical": int(same.sum()),
                        "crud_only": wl.startswith("crud")})
    return pd.DataFrame(out)


# ------------------------------------------------------------------- report

def caps_section(steps: int = 30) -> list[str]:
    """Report lines for the binding-cap probe: published gate, symmetric
    gate, published switching penalty."""
    rows = [("published planner, switching penalty 0", {"switch_penalty": 0.0}),
            ("decoupled gate made symmetric (diagnosis), penalty 0",
             {"switch_penalty": 0.0, "symmetric_gate": True}),
            ("published planner, published penalty 0.05", {"switch_penalty": None})]
    L = ["", "## Binding caps (direct planner, CRUD-only: an additive objective)", "",
         f"crud_steady/uniform/small, cluster replica cap 12 (8 tenants start at 2), {steps} steps. Every "
         "disagreement between the decoupled and the joint best response from identical inputs, classified "
         "(`cap_binding_probe`):", "",
         "| planner | best-response calls | disagreements by reason |", "|---|---:|---|"]
    for label, kw in rows:
        p = cap_binding_probe(steps=steps, **kw)
        reasons = "; ".join(f"{k}: {v}" for k, v in p["by_reason"].items()) or "none"
        L.append(f"| {label} | {p['calls']} | {reasons} |")
    L += ["", "An infeasible anchor is outside Theorem 1 (expected). The out-of-lattice incumbent is an "
          "implementation asymmetry between the arms: a tenant that fell back earlier in the cycle sits at "
          "(replica_min, 0 MB, none), outside its anchored lattice; `_best_for_tenant` keeps an incumbent "
          "only when it is a lattice member (controller.py:1104-1121), `_best_for_tenant_decoupled` keeps "
          "any feasible incumbent (controller.py:1280-1284; knobwise 1330-1334), so the decoupled arm keeps "
          "a move the one-level cache clamp forbids. See README.md."]
    return L


def report(runs: pd.DataFrame, unit: dict, committed: pd.DataFrame, label: str) -> str:
    ok = runs.apply(cell_passes, axis=1)
    controls_ok = bool(ok[runs.expect_zero].all())
    sens_ok = bool(ok[~runs.expect_zero].all())
    exc = unit_exceptions()
    L = [f"# Negative control (WP22 item 1){' -- ' + label if label else ''}", "",
         "Generated by `research/controls/negative_control.py`. Exploratory instrument check, "
         "not a registered result. Configurations, couplings and line references: README.md.", "",
         f"**Verdict: {'PASS' if controls_ok else 'FAIL'}** -- {int(ok[runs.expect_zero].sum())} of "
         f"{int(runs.expect_zero.sum())} control runs pass every check; sensitivity rows "
         f"{'show' if sens_ok else 'DO NOT show'} a positive index.", "",
         "## Unit level", "",
         f"{unit['boxes']:,} random separable objectives (2-4 knobs, product-form infeasible values, "
         f"feasible anchor): largest index {unit['worst_index']:.2e}, largest |loss| "
         f"{unit['worst_loss']:.2e}, per-knob != joint in {unit['disagreements']} boxes.", "",
         "The two exceptions an additive objective admits (outside Theorem 1): a budget on a sum of "
         f"spends gives index {exc['budget'].oscillation:g} yet x^D = {exc['budget'].per_knob} is "
         f"infeasible (x^J = {exc['budget'].joint}); an infeasible anchor leaves the index undefined "
         f"({exc['anchor'].oscillation}) with x^D = {exc['anchor'].per_knob}, x^J = {exc['anchor'].joint}. "
         "The simulator controls are built to exclude both.", "",
         "## Committed campaigns (no new run)", "",
         "jcac_converged against jcac_decoupled, shared seeds, every design point and replication:", "",
         "| export | workload | pairs | bit-identical |", "|---|---|---:|---:|"]
    for r in committed.itertuples():
        L.append(f"| {r.export} | {r.workload} | {r.pairs} | {r.identical} |")
    L += ["", "## Simulator controls", "",
          "Index: worst-case remainder range per decision (planner objective units); rel: divided by "
          "max(1, |phi(x^J)|). Moved: share of decisions whose joint choice changes that knob.", "",
          "| configuration | carrier | runs | decisions | index max | rel max | undefined | x^D != x^J "
          "| planner disagreements | runs identical | moved r / c / t | pass |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for name, g in runs.groupby("control", sort=False):
        undefined = int((g.decisions - g.index_defined).sum())
        L.append(
            f"| {name} | {g.carrier.iloc[0]} | {len(g)} | {int(g.decisions.sum()):,} | "
            f"{g.index_max.max():.2e} | {g.index_max_rel.max():.2e} | {undefined} | "
            f"{int(g.box_disagreements.sum())} | {g.planner_disagreement_share.max():.4f} | "
            f"{int(g.identical.sum())}/{len(g)} | {g.moved_replicas.mean():.2f} / {g.moved_cache.mean():.2f} / "
            f"{g.moved_tier.mean():.2f} | {'yes' if ok[g.index].all() else 'NO'} |")
    bad = runs[runs.expect_zero & ~ok]
    if len(bad):
        L += ["", "## Failures (reproduce with `run_cell`)", ""]
        for r in bad.itertuples():
            L.append(f"- {r.control} {r.workload}/{r.tenant_mix}/{r.cluster_size}, steps {r.steps}, "
                     f"seed {r.seed}: {r.first_offender}; runs identical {r.identical} "
                     f"(max metric diff {r.max_metric_diff:.3g})")
    sens = runs[~runs.expect_zero]
    if len(sens):
        L += ["", "## Sensitivity: the same pipeline where couplings remain", "",
              "| configuration | runs | mean index | max index | x^D != x^J | gain J(dec) - J(joint) |",
              "|---|---:|---:|---:|---:|---:|"]
        for name, g in sens.groupby("control", sort=False):
            L.append(f"| {name} | {len(g)} | {g.index_mean.mean():.3f} | {g.index_max.max():.3f} | "
                     f"{int(g.box_disagreements.sum())} | {(g.J_decoupled - g.J_joint).mean():+.5f} |")
    L += caps_section()
    L.append("")
    return "\n".join(L)


def jobs(steps: int, pop_n: int, smoke: bool) -> list[tuple]:
    out = []
    for ctl in controls(pop_n):
        cells = ctl.cells[:1] if smoke else ctl.cells
        out += [(ctl.name, w, m, s, steps, pop_n) for (w, m, s) in cells]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--pop-workloads", type=int, default=6, help="population tuning workloads per mode-A control")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--smoke", action="store_true", help="one cell per configuration (default 30 steps)")
    ap.add_argument("--report-only", action="store_true", help="re-render the report from the saved runs")
    args = ap.parse_args()
    steps = 30 if args.smoke and args.steps == 120 else args.steps
    tag = "_smoke" if args.smoke else ""
    started = time.time()
    if args.report_only:
        runs = pd.read_csv(cc.DATA / f"negative_control{tag}.csv")
        steps = int(runs.steps.iloc[0])
    else:
        todo = jobs(steps, args.pop_workloads, args.smoke)
        print(f"{len(todo)} cells, {steps} steps, {args.workers} worker(s)", flush=True)
        if args.workers > 1:
            with Pool(args.workers) as pool:
                rows = pool.map(run_cell, todo, chunksize=1)
        else:
            rows = [run_cell(j) for j in todo]
        runs = pd.DataFrame(rows)
        cc.DATA.mkdir(exist_ok=True)
        runs.to_csv(cc.DATA / f"negative_control{tag}.csv", index=False)
    label = f"smoke ({steps} steps, one cell per configuration)" if args.smoke else f"{steps} steps"
    text = report(runs, unit_control(), committed_check(), label)
    (cc.HERE / f"NEGATIVE_CONTROL{tag.upper()}.md").write_text(text, encoding="utf-8")
    passed = bool(runs[runs.expect_zero].apply(cell_passes, axis=1).all())
    print(f"done in {time.time() - started:.0f}s; controls {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
