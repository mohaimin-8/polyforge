"""WP22 item 3: adversarial search for the case that would refute H2
(programme 2). EXPLORATORY.

H2 says the mean interaction index of a regime predicts the gain of joint
over per-knob control. The case that would refute it is a regime with a
SMALL index and a LARGE gain. Theorem 1 bounds each decision's one-step loss
by that decision's index, so such a regime can only come from what the
theorem does not cover: trajectories that separate and compound, decisions
outside its hypotheses (infeasible anchor, per-knob combination infeasible
and repaired), or a gain that is mostly noise. This module searches for it.

REGIME. A point of the search space: the actuation width, the coupling
kappa (wu_ai_scale), the plant's form and economy (ranges below, spanning
the published plant, plant 2's modes A and B and the dose-response design),
a workload family and a cluster size. Planner: `jcac_converged` (anchored,
sweeps to a fixed point) with the joint or the decoupled knob search.

EVALUATION. A regime is run on a panel of P design points, both arms on the
same demand seed per point (common random numbers; the same seeds for every
regime of the search). Per point: gain g = J(decoupled) - J(joint) (J per
tenant-step, `controls_common.objective_j`; positive = joint better) and the
joint arm's mean worst-case index over its scored decisions. Per regime:
G = mean g over the panel, I = mean index over the panel.

OBJECTIVE (maximised; `--objective`):
  constrained (default)  key (1, G) when I <= tau, else (0, -I): the largest
                         gain among regimes whose index is at most tau; an
                         infeasible regime ranks by how close its index is to
                         tau, so hill-climbing walks into the feasible set.
  ratio                  key (1, G / (I + eps)): gain per unit of index. The
                         dose-response pilot's regimes sit near 0.03.
  `--two-sided` uses |G| for G (a large LOSS of joint control at a small
  index mispredicts too). A regime with an undefined index (no decision
  where it is defined) ranks below every regime with one.
A refutation candidate: I <= tau and G >= g* (`--large-gain`), and it must
survive re-evaluation on FRESH seeds (`--confirm`), because the search
selects on noise as well as on signal (the winner's curse).

PROCEDURE. Random search over `--random` regimes, then stochastic
hill-climbing from the best ones: each step proposes `--batch` neighbours
(one or two parameters moved by `--step` of their range, a categorical
redrawn, the width moved to an adjacent value), evaluates them, and moves to
the best if its key improves; `--patience` failures restart from the next
random regime. Everything is seeded (`--seed`). The budget (`--budget`) is
the number of regime evaluations, each P x 2 runs of `--steps` steps.
Population workloads come from the TUNING split only; the evaluation split
is never touched.

    python adversarial_search.py --smoke                     # a tiny check (about a minute)
    python adversarial_search.py --budget 200 --workers 6    # a real search (hours)
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from multiprocessing import Pool

import controls_common as cc

import numpy as np
import pandas as pd

SALT = "wp22_adversarial"
AI_CLASSES = ("ai_cacheable", "ai_uncacheable", "agentic", "flash_ai", "spike_agentic",
              "tier_mixed", "joint_stress", "mmpp_mixed")
MIXES = ("uniform", "premium_heavy", "besteffort_heavy", "whale")
LATENCY = {  # model_form overrides per latency table
    "published": {},
    "monotone_agent": {"agent_latency_small": 937.5, "agent_latency_mid": 2500.0, "agent_latency_large": 6250.0},
    "mode_a": {"tier_latency_small": 261.7, "tier_latency_mid": 751.2, "tier_latency_large": 2155.7},
    "mode_b": {"tier_latency_small": 1551.3, "tier_latency_mid": 2310.2, "tier_latency_large": 3171.5},
}


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # float | log | bool | choice
    low: float = 0.0
    high: float = 1.0
    choices: tuple = ()


SPACE = (
    Param("width", "choice", choices=(1, 2, 4, 8, 16)),
    Param("kappa", "float", 0.0, 3.0),
    Param("wu_tier_mid", "float", 1.0, 3.0),
    Param("wu_tier_large_ratio", "float", 1.0, 2.0),
    Param("congestion_exponent", "float", 0.5, 2.0),
    Param("replica_capacity_wu", "log", 30.0, 2000.0),
    Param("crud_base_scale", "log", 0.01, 3.0),
    Param("crud_on_replicas", "bool"),
    Param("cache_hit_max", "float", 0.0, 0.95),
    Param("cache_half_mb", "log", 16.0, 1024.0),
    Param("cacheable_uniform", "bool"),
    Param("mixture_p95", "bool"),
    Param("attainment_slo", "bool"),
    Param("admission_gate", "bool"),
    Param("p95_tail_f0", "float", 1.0, 1.5),
    Param("p95_tail_b", "float", 0.0, 0.3),  # f0 * 0.05**-b < 3.87 holds on this box
    Param("self_hosted", "bool"),  # True: no per-request price (plant 2 mode B)
    Param("tier_cost_small", "log", 1e-5, 1e-3),
    Param("tier_cost_mid_ratio", "log", 1.0, 30.0),
    Param("tier_cost_large_ratio", "log", 1.0, 30.0),
    Param("replica_cost_usd_hr", "log", 0.01, 1.0),
    Param("latency", "choice", choices=tuple(LATENCY)),
    Param("workload", "choice", choices=AI_CLASSES + ("population",)),
    Param("cluster_size", "choice", choices=("small", "medium", "large")),
)
BY_NAME = {p.name: p for p in SPACE}


# ------------------------------------------------------------ the space

def sample(rng: random.Random, max_width: int = 16) -> dict:
    out = {}
    for p in SPACE:
        if p.kind == "float":
            out[p.name] = rng.uniform(p.low, p.high)
        elif p.kind == "log":
            out[p.name] = math.exp(rng.uniform(math.log(p.low), math.log(p.high)))
        elif p.kind == "bool":
            out[p.name] = rng.random() < 0.5
        else:
            choices = [c for c in p.choices if p.name != "width" or c <= max_width]
            out[p.name] = rng.choice(choices)
    return out


def neighbour(theta: dict, rng: random.Random, step: float = 0.15, max_width: int = 16) -> dict:
    """One or two parameters moved: a float by +/- step of its range, a log
    parameter by a factor (high / low) ** step, a bool flipped, the width to
    an adjacent allowed value, another categorical redrawn."""
    out = dict(theta)
    for p in rng.sample(SPACE, rng.choice((1, 2))):
        v = out[p.name]
        if p.kind == "float":
            out[p.name] = min(p.high, max(p.low, v + rng.choice((-1, 1)) * step * (p.high - p.low)))
        elif p.kind == "log":
            f = (p.high / p.low) ** (step * rng.choice((-1, 1)))
            out[p.name] = min(p.high, max(p.low, v * f))
        elif p.kind == "bool":
            out[p.name] = not v
        elif p.name == "width":
            allowed = [c for c in p.choices if c <= max_width]
            i = allowed.index(v) if v in allowed else 0
            out[p.name] = allowed[min(len(allowed) - 1, max(0, i + rng.choice((-1, 1))))]
        else:
            out[p.name] = rng.choice([c for c in p.choices if c != v] or [v])
    return out


def plant_of(theta: dict) -> tuple[dict, dict]:
    """(model_form, economy) of a regime, in the harness's override keys."""
    form = {
        "wu_ai_scale": theta["kappa"],
        "wu_tier_mid": theta["wu_tier_mid"],
        "wu_tier_large": theta["wu_tier_mid"] * theta["wu_tier_large_ratio"],
        "congestion_exponent": theta["congestion_exponent"],
        "replica_capacity_wu": theta["replica_capacity_wu"],
        "crud_base_scale": theta["crud_base_scale"],
        "wu_crud_scale": 1.0 if theta["crud_on_replicas"] else 0.0,
        "cacheable_uniform": int(theta["cacheable_uniform"]),
        "mixture_p95": int(theta["mixture_p95"]),
        "attainment_slo": int(theta["attainment_slo"]),
        "admission_gate": int(theta["admission_gate"]),
        "p95_tail_f0": theta["p95_tail_f0"],
        "p95_tail_b": theta["p95_tail_b"],
        **LATENCY[theta["latency"]],
    }
    small = 0.0 if theta["self_hosted"] else theta["tier_cost_small"]
    mid = small * theta["tier_cost_mid_ratio"]
    economy = {
        "cache_hit_max": theta["cache_hit_max"], "cache_half_mb": theta["cache_half_mb"],
        "tier_cost_small": small, "tier_cost_mid": mid, "tier_cost_large": mid * theta["tier_cost_large_ratio"],
        "replica_cost_usd_hr": theta["replica_cost_usd_hr"],
    }
    return form, economy


def panel_of(theta: dict, panel: int) -> list[tuple]:
    """The regime's design points: a synthetic class under the first
    `panel` mixes, or `panel` population workloads of the TUNING split (a
    fixed draw), each with its own size."""
    size = theta["cluster_size"]
    if theta["workload"] != "population":
        return [(theta["workload"], m, size) for m in MIXES[:panel]]
    from harness import population_workloads

    names = population_workloads.names("tuning")
    random.Random(SALT).shuffle(names)
    return [(w, "own", size) for w in names[:panel]]


# ------------------------------------------------------------ evaluation

def eval_point(job: tuple) -> dict:
    """Both arms of one design point of one regime (a pooled-worker job).
    Global state: the arms are registered for this call only; the plant is
    set by the harness from the RunSpec at the start of every run (it
    resets first), so no job inherits another's plant or arms."""
    cid, theta, (workload, mix, size), steps, rep = job
    form, economy = plant_of(theta)
    seed = cc.design_seed(SALT, workload, mix, size, rep)
    runs = cc.run_specs("wp22_adversarial", workload, mix, size, steps, seed, economy, form)
    arms = cc.arm_pair(cc.width_params(theta["width"]), track=False)
    started = time.time()
    out = cc.execute_pair(runs, arms)
    mj, md = out["joint"]["metrics"], out["decoupled"]["metrics"]
    jj, jd = cc.objective_j(mj, out["joint"]["tenants"]), cc.objective_j(md, out["decoupled"]["tenants"])
    return {"cid": cid, "workload": workload, "tenant_mix": mix, "cluster_size": size, "rep": rep,
            "seed": seed, "steps": steps, "wall_s": time.time() - started,
            "J_joint": jj, "J_decoupled": jd, "gain": jd - jj,
            "repaired_share_dec": md["plan_repaired_share"], "fallback_share_dec": md["plan_fallback_share"],
            "fallback_share_joint": mj["plan_fallback_share"], **cc.summarise_rows(out["rows"], steps)}


def summarise(cid: int, theta: dict, points: list[dict], phase: str) -> dict:
    g = np.array([p["gain"] for p in points], float)
    idx = np.array([p["index_mean"] for p in points], float)
    loss = np.array([p["loss_mean"] for p in points], float)

    def nanmean(a):
        a = a[~np.isnan(a)]
        return float(a.mean()) if a.size else math.nan

    return {"cid": cid, "phase": phase, **theta,
            "points": len(points), "G": float(g.mean()),
            "G_se": float(g.std(ddof=1) / math.sqrt(len(g))) if len(g) > 1 else math.nan,
            "I": nanmean(idx), "I_quantile": nanmean(np.array([p["quantile_index_mean"] for p in points], float)),
            "loss": nanmean(loss), "costly": nanmean(np.array([p["costly_share"] for p in points], float)),
            "per_knob_infeasible": nanmean(np.array([p["per_knob_infeasible_share"] for p in points], float)),
            "anchor_infeasible": nanmean(np.array([p["anchor_infeasible_share"] for p in points], float)),
            "repaired_dec": nanmean(np.array([p["repaired_share_dec"] or 0.0 for p in points], float)),
            "theorem_violations": int(sum(p["theorem_violations"] for p in points)),
            "wall_s": float(sum(p["wall_s"] for p in points))}


def objective_key(row: dict, mode: str, tau: float, eps: float, two_sided: bool) -> tuple:
    """The search maximises this key; see the module docstring."""
    g = abs(row["G"]) if two_sided else row["G"]
    i = row["I"]
    if i is None or math.isnan(i):
        return (-1, -math.inf)
    if mode == "ratio":
        return (1, g / (i + eps))
    return (1, g) if i <= tau else (0, -i)


class Evaluator:
    """Evaluates regimes (in parallel when `workers` > 1) and keeps every
    design-point row and regime summary."""

    def __init__(self, args, pool):
        self.args, self.pool = args, pool
        self.points: list[dict] = []
        self.regimes: list[dict] = []
        self.next_cid = 0

    def evaluate(self, thetas: list[dict], phase: str, reps=(0,), steps: int | None = None) -> list[dict]:
        steps = steps or self.args.steps
        jobs, cids = [], []
        for theta in thetas:
            cid, self.next_cid = self.next_cid, self.next_cid + 1
            cids.append(cid)
            jobs += [(cid, theta, dp, steps, rep) for dp in panel_of(theta, self.args.panel) for rep in reps]
        rows = (self.pool.map(eval_point, jobs, chunksize=1) if self.pool is not None
                else [eval_point(j) for j in jobs])
        self.points += [{**r, "phase": phase} for r in rows]
        out = []
        for cid, theta in zip(cids, thetas):
            s = summarise(cid, theta, [r for r in rows if r["cid"] == cid], phase)
            s["key"] = objective_key(s, self.args.objective, self.args.tau, self.args.eps, self.args.two_sided)
            out.append(s)
        self.regimes += out
        return out


def search(args, pool) -> Evaluator:
    rng = random.Random(args.seed)
    ev = Evaluator(args, pool)
    n_random = min(args.budget, args.random if args.random is not None else max(1, args.budget // 2))
    starts = sorted(ev.evaluate([sample(rng, args.max_width) for _ in range(n_random)], "random"),
                    key=lambda r: r["key"], reverse=True)
    used = n_random
    for start in starts:
        if used >= args.budget:
            break
        current, fails = start, 0
        while used < args.budget and fails < args.patience:
            batch = min(args.batch, args.budget - used)
            theta0 = {p.name: current[p.name] for p in SPACE}
            cands = ev.evaluate([neighbour(theta0, rng, args.step, args.max_width) for _ in range(batch)],
                                f"climb from {start['cid']}")
            used += batch
            best = max(cands, key=lambda r: r["key"])
            if best["key"] > current["key"]:
                current, fails = best, 0
            else:
                fails += 1
    return ev


def confirm(ev: Evaluator, args) -> list[dict]:
    """Re-evaluate the best regimes on fresh seeds (reps 1..n), the honest
    number after a search that selected on the old ones."""
    if args.confirm <= 0 or args.confirm_reps <= 0:
        return []
    top = sorted((r for r in ev.regimes if r["key"][0] >= 0), key=lambda r: r["key"], reverse=True)[:args.confirm]
    out = []
    for r in top:
        theta = {p.name: r[p.name] for p in SPACE}
        c = ev.evaluate([theta], f"confirm {r['cid']}", reps=tuple(range(1, args.confirm_reps + 1)),
                        steps=args.confirm_steps or args.steps)[0]
        out.append({"of": r["cid"], **c})
    return out


# ---------------------------------------------------------------- report

def _spearman(a, b) -> float:
    s = pd.DataFrame({"a": a, "b": b}).dropna()
    return float(s.a.rank().corr(s.b.rank())) if len(s) >= 3 else math.nan


def _same_runs(pts: pd.DataFrame, cids) -> dict:
    """cid -> the first earlier cid in `cids` whose evaluation gave the same J
    in every run, in both arms (same workloads, reps and seeds): the same
    evidence again, whatever parameters it changes."""
    seen, out = [], {}
    for cid in cids:
        runs = pts[pts.cid == cid].sort_values(["workload", "rep", "seed"])
        if not len(runs):
            continue
        key = tuple(zip(runs.workload, runs.rep, runs.seed))
        js = runs[["J_joint", "J_decoupled"]].to_numpy(float)
        match = next((c for c, k, j in seen if k == key and np.array_equal(j, js)), None)
        if match is None:
            seen.append((cid, key, js))
        else:
            out[cid] = match
    return out


SAME_RUNS = ("*Same runs as*: {where}every run of that regime gave the same J, in both arms, as the regime "
             "named (the parameters it changes changed no run)")


def _corner_lines(corner: pd.DataFrame, pts: pd.DataFrame, args) -> list[str]:
    """How many searched regimes reached the corner, and how many of them hold runs of their own."""
    if not len(corner):
        return [f"No evaluated regime reached the refutation corner (I <= {args.tau:g} with "
                f"G >= {args.large_gain:g})."]
    repeats = len(_same_runs(pts, list(corner.cid)))
    phases = sorted(corner.phase.unique())
    where = f"all from one phase, {phases[0]}" if len(phases) == 1 else f"from {len(phases)} phases"
    return [f"**{len(corner)} regime(s) in the refutation corner during the search** (before confirmation): "
            f"{len(corner) - repeats} with runs of their own, {repeats} repeating another's exactly; {where}."]


def _worst_case_lines(best) -> list[str]:
    if best is None:
        return []
    params = ", ".join(f"{p.name}={best[p.name]:.4g}" if isinstance(best[p.name], float)
                       else f"{p.name}={best[p.name]}" for p in SPACE)
    return ["", f"Worst case for H2 by the objective (regime {best.cid}, {best.phase}): G {best.G:+.4f} "
            f"(se {best.G_se:.4f}), I {best.I:.4f}, quantile index {best.I_quantile:.4f}, one-step loss "
            f"{best.loss:.5f}, costly disagreements {best.costly:.2%}, per-knob infeasible "
            f"{best.per_knob_infeasible:.2%}, anchor infeasible {best.anchor_infeasible:.2%}, "
            f"decoupled repairs {best.repaired_dec:.2%}.", "", f"Parameters: {params}"]


def _confirmation_lines(regs: pd.DataFrame, pts: pd.DataFrame, confirmed: list[dict], args) -> list[str]:
    """The confirmation table; a confirmation that repeats another's fresh runs is marked and counted once."""
    of = {c["cid"]: c["of"] for c in confirmed}
    again = _same_runs(pts, [c["cid"] for c in confirmed])
    L = ["", "## Confirmation on fresh seeds", "",
         "| regime | search G | search I | fresh G | fresh se | fresh I | still in corner | same runs as |",
         "|---:|---:|---:|---:|---:|---:|---|---:|"]
    for c in confirmed:
        s = regs[regs.cid == c["of"]].iloc[0]
        gc = abs(c["G"]) if args.two_sided else c["G"]
        inside = c["I"] <= args.tau and gc >= args.large_gain
        L.append(f"| {c['of']} | {s.G:+.4f} | {s.I:.4f} | {c['G']:+.4f} | {c['G_se']:.4f} | "
                 f"{c['I']:.4f} | {'YES' if inside else 'no'} | {of[again[c['cid']]] if c['cid'] in again else '-'} |")
    if again:
        distinct = len(confirmed) - len(again)
        L += ["", f"{SAME_RUNS.format(where='on the fresh seeds, ')}; the table holds {distinct} distinct "
              f"confirmation{'s' if distinct != 1 else ''} of {len(confirmed)}."]
    return L


def _top_lines(top: pd.DataFrame, pts: pd.DataFrame) -> list[str]:
    again = _same_runs(pts, list(top.cid))
    L = ["", "## Top regimes by the objective", "",
         "| regime | phase | width | kappa | workload | size | I | G | loss | per-knob infeasible | same runs as |",
         "|---:|---|---:|---:|---|---|---:|---:|---:|---:|---:|"]
    for r in top.itertuples():
        L.append(f"| {r.cid} | {r.phase} | {r.width} | {r.kappa:.2f} | {r.workload} | {r.cluster_size} | "
                 f"{r.I:.4f} | {r.G:+.4f} | {r.loss:.5f} | {r.per_knob_infeasible:.2%} | {again.get(r.cid, '-')} |")
    if again:
        L += ["", f"{SAME_RUNS.format(where='')}."]
    return L


def report(ev: Evaluator, confirmed: list[dict], args, label: str) -> str:
    ranked = sorted((r for r in ev.regimes if not r["phase"].startswith("confirm")),
                    key=lambda r: r["key"], reverse=True)  # best first, by the objective
    regs = pd.DataFrame(ranked)
    pts = pd.DataFrame(ev.points)
    g = regs.G.abs() if args.two_sided else regs.G
    corner = regs[(regs.I <= args.tau) & (g >= args.large_gain)]
    best = regs.iloc[0] if len(regs) else None
    L = [f"# Adversarial search for H2 (WP22 item 3){' -- ' + label if label else ''}", "",
         "Generated by `research/controls/adversarial_search.py`. EXPLORATORY: a search selects on noise "
         "as well as signal, so only the confirmation on fresh seeds below is a number to quote.", "",
         "## Design", "",
         f"- objective: `{args.objective}`{' (two-sided |G|)' if args.two_sided else ''}; tau {args.tau:g}, "
         f"eps {args.eps:g}; refutation corner: I <= {args.tau:g} and G >= {args.large_gain:g}",
         f"- budget {args.budget} regime evaluations ({(regs.phase == 'random').sum()} random, "
         f"{(regs.phase != 'random').sum()} hill-climbing); panel {args.panel} design point(s); "
         f"{args.steps} steps; max width {args.max_width}; seed {args.seed}",
         f"- runs: {len(pts) * 2} ({pts.wall_s.sum():.0f} s of run time); decisions logged "
         f"{int(pts.decisions.sum()):,}; Theorem 1 checked on {int(pts.theorem_applies.sum()):,}, "
         f"violations **{int(pts.theorem_violations.sum())}**", "",
         "## Result", ""]
    L += _corner_lines(corner, pts, args) + _worst_case_lines(best)
    if confirmed:
        L += _confirmation_lines(regs, pts, confirmed, args)
    L += _top_lines(regs.head(8), pts)
    L += ["", f"Across the {len(regs)} regimes searched (not a test of H2: the sample is selected), rank "
          f"correlation of I with G: {_spearman(regs.I, regs.G):+.2f}; of the one-step loss with G: "
          f"{_spearman(regs.loss, regs.G):+.2f}.", ""]
    return "\n".join(L)


def from_record(tag: str) -> tuple:
    """(evaluations, confirmations, arguments, label) of a committed search, as
    `report` reads them: its points, regimes and arguments under data/."""
    stem = f"adversarial_{tag}"
    args = argparse.Namespace(**json.loads((cc.DATA / f"{stem}_args.json").read_text(encoding="utf-8")))
    points = pd.read_csv(cc.DATA / f"{stem}_points.csv", float_precision="round_trip").to_dict(orient="records")
    regimes = pd.read_csv(cc.DATA / f"{stem}_regimes.csv", float_precision="round_trip").to_dict(orient="records")
    for r in regimes:
        r["key"] = objective_key(r, args.objective, args.tau, args.eps, args.two_sided)
    confirmed = [{"of": int(r["phase"].split()[1]), **r} for r in regimes if r["phase"].startswith("confirm")]
    label = "SMOKE: checks the machinery, not evidence" if args.smoke else ""
    return argparse.Namespace(points=points, regimes=regimes), confirmed, args, label


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--budget", type=int, default=200, help="regime evaluations in all")
    ap.add_argument("--random", type=int, default=None, help="random regimes first (default budget // 2)")
    ap.add_argument("--panel", type=int, default=4, help="design points per regime")
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--max-width", type=int, default=16)
    ap.add_argument("--objective", choices=("constrained", "ratio"), default="constrained")
    ap.add_argument("--two-sided", action="store_true")
    ap.add_argument("--tau", type=float, default=0.25, help="small-index threshold")
    ap.add_argument("--eps", type=float, default=0.05, help="index floor of the ratio objective")
    ap.add_argument("--large-gain", type=float, default=0.03, help="g*: a large gain")
    ap.add_argument("--batch", type=int, default=6, help="neighbours per hill-climbing step")
    ap.add_argument("--patience", type=int, default=3)
    ap.add_argument("--step", type=float, default=0.15)
    ap.add_argument("--confirm", type=int, default=3, help="best regimes re-run on fresh seeds")
    ap.add_argument("--confirm-reps", type=int, default=2)
    ap.add_argument("--confirm-steps", type=int, default=None)
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--tag", default="search")
    ap.add_argument("--smoke", action="store_true",
                    help="budget 3, panel 1, 10 steps, width <= 2, one confirmation rep; tag 'smoke'")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild ADVERSARIAL_<TAG>.md from the committed record under data/ (no runs)")
    args = ap.parse_args(argv)
    if args.smoke:
        args.budget, args.random, args.panel, args.steps = 3, 2, 1, 10
        args.max_width, args.batch, args.patience = 2, 1, 1
        args.confirm, args.confirm_reps, args.tag = 1, 1, "smoke"
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.report_only:
        ev, confirmed, stored, label = from_record(args.tag)
        (cc.HERE / f"ADVERSARIAL_{args.tag.upper()}.md").write_text(report(ev, confirmed, stored, label),
                                                                  encoding="utf-8")
        return 0
    started = time.time()
    pool = Pool(args.workers) if args.workers > 1 else None
    try:
        ev = search(args, pool)
        confirmed = confirm(ev, args)
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    cc.DATA.mkdir(exist_ok=True)
    stem = f"adversarial_{args.tag}"
    pd.DataFrame(ev.points).to_csv(cc.DATA / f"{stem}_points.csv", index=False)
    pd.DataFrame(ev.regimes).drop(columns=["key"]).to_csv(cc.DATA / f"{stem}_regimes.csv", index=False)
    (cc.DATA / f"{stem}_args.json").write_text(json.dumps(vars(args), indent=2), encoding="utf-8")
    label = "SMOKE: checks the machinery, not evidence" if args.smoke else ""
    (cc.HERE / f"ADVERSARIAL_{args.tag.upper()}.md").write_text(report(ev, confirmed, args, label),
                                                              encoding="utf-8")
    print(f"done in {time.time() - started:.0f}s; {len(ev.regimes)} regime evaluations; "
          f"theorem violations {sum(r['theorem_violations'] for r in ev.regimes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
