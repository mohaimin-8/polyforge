"""RouteBalance's per-request decision as a box for the interaction index (programme 2, WP15).

Source: W. Da and E. Kalyvianaki, "RouteBalance: Fused Model Routing and Load Balancing for
Heterogeneous LLM Serving", arXiv 2606.17949v2 (31 Oct 2026), and their code, github.com/AKafakA/
route-balance, branch `submission` at ac49feb (read, never run). NOTES.md maps every line below to
the paper and to their code.

One decision: request r of a scheduling round chooses one of the fleet's 13 instances, which fixes
the model. RouteBalance maximises (paper Eq. 2-3; routebalance/schedule/score.py:97-101)

    S = w_q Q + w_c C + w_l T,    Q = (q - q_min) / (q_max - q_min + eps),
    C = 1 - c / (c_max + eps),    T = 1 - l / (l_max + eps),

with the latency estimate l of paper Eq. 1 (routebalance/estimate/latency.py:244-249 and 297). This
module minimises the penalty phi = 1 - S, which lies in [0, 1].

The box. The fused decision is a choice among 13 (model, instance) pairs, which is not a product of
two lists. The index needs one, so the instance is written as its rank within its model:
    knob 1, the model (4 values, fleet order);
    knob 2, the rank of the instance within that model, 0..4, in the balancer's own order
        ("balancer": fewest engine-reported requests, then least recently assigned, then fleet
        order, as least outstanding picks; routebalance/baselines/dispatch.py:91-105), or by the
        fused latency estimate ("latency", a sensitivity variant).
(m, j) is infeasible when model m has at most j instances. Each of the 13 candidates is exactly one
feasible point, so the joint minimiser is RouteBalance's own choice (up to ties).

The anchor is the decoupled choice, RB-decoupled (paper Sec. 4): the router scores the four tiers
with RouteBalance's objective on idle-instance latency, normalised over the request's own candidates
(routebalance/baselines/routers.py:483-506), and least outstanding places the request on that
model's rank-0 instance.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent
_THEORY = HERE.parents[1] / "theory"
if str(_THEORY) not in sys.path:
    sys.path.insert(0, str(_THEORY))

from interaction_index import (analyse_table, coordinatewise_minimum,  # noqa: E402
                               remainder_table)

EPS = 1e-8                 # paper Sec. 3.5; routebalance/schedule/score.py:28
AUTO_EXTEND_TOKENS = 10    # routebalance/telemetry.py: a request past its prediction is extended by 10


@dataclass(frozen=True)
class Tier:
    """One (model, GPU) tier. Prices and max_num_seqs: data pack data/fleet_qwen.json; TPOT (the
    constant RouteBalance scores with): data/tpot_qwen.json "constant"; prefill line: data/
    prefill_qwen.json (paper Table 3, tab:cluster)."""
    tier_id: str
    model: str
    cost_in: float          # USD per prompt token
    cost_out: float         # USD per output token
    max_num_seqs: int
    tpot_s: float           # per-token decode time
    prefill_a_s: float      # prefill intercept
    prefill_b_s: float      # prefill seconds per prompt token


# Fleet order of tiers (routebalance/server.py:132-133 takes them in instance order).
TIERS: tuple[Tier, ...] = (
    Tier("qwen2.5-14b_v100", "Qwen/Qwen2.5-14B", 1.5e-07, 1.5e-07, 256,
         20.9122020065534e-3, 33.02760041056693e-3, 0.129427848840815e-3),
    Tier("qwen2.5-3b_a30", "Qwen/Qwen2.5-3B", 6e-08, 6e-08, 256,
         11.351072999377124e-3, 18.11298053373232e-3, 0.05361169051610872e-3),
    Tier("qwen2.5-72b_a100", "Qwen/Qwen2.5-72B", 3.8e-07, 4e-07, 256,
         44.346297208957274e-3, 77.78767705145796e-3, 0.13091304330023695e-3),
    Tier("qwen2.5-7b_a30", "Qwen/Qwen2.5-7B", 7e-08, 7e-08, 256,
         22.01794186842626e-3, 33.88362128522988e-3, 0.10838609588680147e-3),
)
# The 13 instances in fleet order, as tier indices (data/fleet_qwen.json "instances").
INSTANCE_TIER: tuple[int, ...] = (0, 0, 0, 1, 1, 1, 2, 2, 3, 3, 3, 3, 3)
MODELS: tuple[str, ...] = tuple(t.model for t in TIERS)      # one tier per model in this fleet
SHORT = ("14B", "3B", "72B", "7B")
RANKS: tuple[int, ...] = tuple(range(max(INSTANCE_TIER.count(k) for k in range(len(TIERS)))))
# Weight presets of the paper (Sec. 5.1), as (w_quality, w_latency, w_cost).
PRESETS: dict[str, tuple[float, float, float]] = {
    "uniform": (1 / 3, 1 / 3, 1 / 3), "50/30/20": (0.5, 0.3, 0.2), "60/20/20": (0.6, 0.2, 0.2),
    "80/10/10": (0.8, 0.1, 0.1), "90/5/5": (0.9, 0.05, 0.05), "5/25/70": (0.05, 0.25, 0.7),
    "22/18/60": (0.22, 0.18, 0.6), "25/5/70": (0.25, 0.05, 0.7)}
ORDERS = ("balancer", "latency")


@dataclass(frozen=True)
class InstanceState:
    """What the two schedulers see of one instance when the request is decided."""
    running: int            # RouteBalance's view (dead reckoning: min(engine gauge, in flight))
    waiting: int            # in flight minus running, plus this round's earlier assignments
    pending: int            # pending decode tokens D (scheduler-side, from streamed progress)
    lor_count: int          # running + waiting as the balancer's 50 ms poll cache reports it
    last_assigned: float    # when the instance last won a request; 0.0 = never


@dataclass(frozen=True)
class Request:
    prompt_tokens: int
    quality: tuple[float, ...]   # estimated quality per model (MODELS order)
    length: tuple[float, ...]    # expected output tokens per model


@dataclass(frozen=True)
class Scales:
    """Normalisers of one decision: over the round (RouteBalance) or over the request's own
    candidates (the per-request ablation and the decoupled router)."""
    q_min: float
    q_max: float
    c_max: float
    l_max: float


# ---------------------------------------------------------------- their formulas

def own_tokens(length: float) -> int:
    """queue_aware's own length: max(1, int(L)) (latency.py:246)."""
    return max(1, int(length))


def latency_s(tier: Tier, state: InstanceState, length: float, prompt_tokens: int) -> float:
    """Paper Eq. 1: P_t(p) + tau_t (own + delta D / max(n, 1)), delta = 1 when the instance has a
    waiting request or a full batch (latency.py:244-249 plus the prefill addend, :297)."""
    own = own_tokens(length)
    queued = state.running >= tier.max_num_seqs or state.waiting > 0
    decode = (state.pending / max(state.running, 1) + own) * tier.tpot_s if queued else own * tier.tpot_s
    return decode + tier.prefill_a_s + tier.prefill_b_s * prompt_tokens


def idle_latency_s(tier: Tier, length: float, prompt_tokens: int) -> float:
    """The same estimate on an idle instance: what RB-decoupled's router scores (server.py:320-322)."""
    return own_tokens(length) * tier.tpot_s + tier.prefill_a_s + tier.prefill_b_s * prompt_tokens


def cost_usd(tier: Tier, length: float, prompt_tokens: int) -> float:
    """The prompt at the input price plus the expected output at the output price (assign.py:156)."""
    return prompt_tokens * tier.cost_in + length * tier.cost_out


def penalty(weights: tuple[float, float, float], quality: float, cost: float, latency: float,
            scales: Scales) -> float:
    """1 - S, with S summed in their order: quality, cost, latency (score.py:97-101)."""
    w_q, w_l, w_c = weights
    q = (quality - scales.q_min) / (scales.q_max - scales.q_min + EPS)
    c = 1.0 - cost / (scales.c_max + EPS)
    lat = 1.0 - latency / (scales.l_max + EPS)
    return 1.0 - (w_q * q + w_c * c + w_l * lat)


def request_scales(request: Request, latencies: Sequence[float]) -> Scales:
    """Normalising over the request's own candidates (score.py:85-89)."""
    qs = [request.quality[INSTANCE_TIER[i]] for i in range(len(INSTANCE_TIER))]
    cs = [cost_usd(TIERS[k], request.length[k], request.prompt_tokens) for k in INSTANCE_TIER]
    return Scales(min(qs), max(qs), max(cs), max(latencies))


def round_scales(requests: Sequence[Request], l_run: float) -> Scales:
    """The round's quality range and cost maximum over every request x instance (assign.py:142-146),
    with the running latency maximum l_run (assign.py:170: it must already include the current
    request's candidates)."""
    qs = [q for r in requests for q in r.quality]
    cs = [cost_usd(t, r.length[k], r.prompt_tokens) for r in requests for k, t in enumerate(TIERS)]
    return Scales(min(qs), max(qs), max(cs), l_run)


def decoupled_model(request: Request, weights: tuple[float, float, float]) -> int:
    """RB-decoupled's router (routers.py:483-506): every tier scored on idle latency, normalised over
    the four tiers; the first tier in fleet order wins exact ties. Returns the model index."""
    lat = [idle_latency_s(t, request.length[k], request.prompt_tokens) for k, t in enumerate(TIERS)]
    cost = [cost_usd(t, request.length[k], request.prompt_tokens) for k, t in enumerate(TIERS)]
    sc = Scales(min(request.quality), max(request.quality), max(cost), max(lat))
    pens = [penalty(weights, request.quality[k], cost[k], lat[k], sc) for k in range(len(TIERS))]
    return min(range(len(TIERS)), key=lambda k: (pens[k], k))


def balancer_key(states: Sequence[InstanceState], i: int) -> tuple:
    """Least outstanding's key (dispatch.py:101-103): engine count, then least recently assigned;
    Python's min keeps fleet order among the rest."""
    return (states[i].lor_count, states[i].last_assigned, i)


# ---------------------------------------------------------------- one decision

@dataclass(frozen=True)
class Decision:
    request: Request
    states: tuple[InstanceState, ...]          # per instance, fleet order
    scales: Scales
    weights: tuple[float, float, float]        # (w_quality, w_latency, w_cost)
    order: str = "balancer"

    def __post_init__(self):
        if len(self.states) != len(INSTANCE_TIER):
            raise ValueError(f"need {len(INSTANCE_TIER)} instance states, got {len(self.states)}")
        if self.order not in ORDERS:
            raise ValueError(f"order must be one of {ORDERS}")

    @cached_property
    def latencies(self) -> tuple[float, ...]:
        r = self.request
        return tuple(latency_s(TIERS[k], s, r.length[k], r.prompt_tokens)
                     for k, s in zip(INSTANCE_TIER, self.states))

    @cached_property
    def penalties(self) -> tuple[float, ...]:
        r = self.request
        return tuple(penalty(self.weights, r.quality[k], cost_usd(TIERS[k], r.length[k], r.prompt_tokens),
                             lat, self.scales)
                     for k, lat in zip(INSTANCE_TIER, self.latencies))

    @cached_property
    def ranked(self) -> tuple[tuple[int, ...], ...]:
        """Per model, its instances in the box's rank order."""
        out = []
        for k in range(len(TIERS)):
            insts = [i for i, t in enumerate(INSTANCE_TIER) if t == k]
            if self.order == "balancer":
                insts.sort(key=lambda i: balancer_key(self.states, i))
            else:
                insts.sort(key=lambda i: (self.latencies[i], i))
            out.append(tuple(insts))
        return tuple(out)

    def instance(self, point: tuple) -> int | None:
        m, j = point
        k = MODELS.index(m)
        return self.ranked[k][j] if j < len(self.ranked[k]) else None

    def phi(self, point: tuple) -> float | None:
        i = self.instance(point)
        return None if i is None else self.penalties[i]

    @property
    def values(self) -> list[list]:
        return [list(MODELS), list(RANKS)]

    @cached_property
    def anchor(self) -> tuple:
        """RB-decoupled's choice: its router's model at the balancer's pick."""
        k = decoupled_model(self.request, self.weights)
        lor = min((i for i, t in enumerate(INSTANCE_TIER) if t == k),
                  key=lambda i: balancer_key(self.states, i))
        return (MODELS[k], self.ranked[k].index(lor))

    def fused_instance(self) -> int:
        """RouteBalance's pick with its tie-break: score, shorter queue, least recently assigned,
        fleet order (score.py:105-107)."""
        return min(range(len(INSTANCE_TIER)),
                   key=lambda i: (self.penalties[i], self.states[i].running + self.states[i].waiting,
                                  self.states[i].last_assigned, i))

    def sequential(self) -> tuple:
        """Router first with the balancer held at the anchor's rank, then the balancer given the
        router's model, both on the shared objective."""
        j0 = self.anchor[1]
        m = min((mm for mm in MODELS if self.phi((mm, j0)) is not None),
                key=lambda mm: (self.phi((mm, j0)), MODELS.index(mm)))
        k = MODELS.index(m)
        j = min(range(len(self.ranked[k])), key=lambda jj: (self.phi((m, jj)), jj))
        return (m, j)

    def probe_record(self, rid: str) -> dict:
        """The decision in tools/index-probe's log format (every candidate's objective value)."""
        return {"id": rid, "values": self.values, "anchor": list(self.anchor),
                "phi": [[list(x), self.phi(x)] for x in itertools.product(MODELS, RANKS)],
                "chosen": [MODELS[INSTANCE_TIER[self.fused_instance()]],
                           self.ranked[INSTANCE_TIER[self.fused_instance()]].index(self.fused_instance())]}


def analyse(d: Decision) -> dict:
    """Index, per-knob loss (Theorem 1), sequential and iterated (Theorem 2) losses and the fused gain
    over the decoupled choice, for one decision. Objective units: the penalty 1 - S."""
    values = d.values
    table = remainder_table(values, d.phi, d.anchor)
    a = analyse_table(values, table)
    xs = d.sequential()
    xc = coordinatewise_minimum(values, d.phi, d.anchor)
    feas = [v for v in table.phi.values() if v is not None]
    pj, p0 = a.phi_joint, a.phi_anchor
    queued = sum(1 for s in d.states if s.waiting > 0 or s.running >= 256)
    return {
        "index": a.oscillation, "quantile_index": a.quantile_index,
        "phi_joint": pj, "phi_anchor": p0, "spread": max(feas) - min(feas),
        "loss_perknob": a.loss, "bound": a.bound, "theorem1_applies": a.theorem1_applies,
        "needs_repair": a.phi_per_knob is None,
        "loss_sequential": d.phi(xs) - pj, "loss_iterated": d.phi(xc) - pj,
        "gain_fused": p0 - pj, "gain_main": (p0 - a.phi_per_knob) if a.phi_per_knob is not None else None,
        "anchor": d.anchor, "per_knob": a.per_knob, "sequential": xs, "joint": a.joint,
        "disagree_perknob": a.per_knob != a.joint, "disagree_anchor": d.anchor != a.joint,
        "model_differs": d.anchor[0] != a.joint[0], "queued_instances": queued,
    }


# ---------------------------------------------------------------- stated synthetic ranges

# Mean judged quality per model on the Qwen evaluation corpus (data pack data/corpus_stats.json) and
# mean output length per model on the evaluation labels (data/labels/qwen_eval.jsonl).
SYNTH_QUALITY = (0.3976, 0.3510, 0.4322, 0.3350)
SYNTH_LENGTH = (170.4, 208.2, 198.2, 255.1)
SYNTH_RANGES = {   # stated, not measured: a busy but unsaturated fleet
    "quality_sd": 0.12, "length_log_sd": 0.6, "prompt_log_mean": math.log(70.0), "prompt_log_sd": 0.8,
    "round_size": (1, 6), "running": (0, 60), "waiting_prob": 0.3, "waiting": (1, 3),
    "pending_per_running": (30.0, 250.0), "lor_hidden_prob": 0.5, "l_run_factor": (0.5, 1.5)}


def _synthetic_request(rng: random.Random, r: dict) -> Request:
    q = tuple(min(1.0, max(0.0, rng.gauss(mu, r["quality_sd"]))) for mu in SYNTH_QUALITY)
    length = tuple(min(1024.0, mu * math.exp(rng.gauss(-0.5 * r["length_log_sd"] ** 2, r["length_log_sd"])))
                   for mu in SYNTH_LENGTH)
    p = max(1, int(math.exp(rng.gauss(r["prompt_log_mean"], r["prompt_log_sd"]))))
    return Request(p, q, length)


def _synthetic_state(rng: random.Random, r: dict, mean_length: float) -> InstanceState:
    n = rng.randint(*r["running"])
    w = rng.randint(*r["waiting"]) if rng.random() < r["waiting_prob"] else 0
    pending = int(n * rng.uniform(*r["pending_per_running"]) + w * mean_length)
    hidden = rng.randint(0, w) if rng.random() < r["lor_hidden_prob"] else 0
    return InstanceState(n, w, pending, n + w - hidden, rng.random())


def synthetic_decisions(n: int, seed: int, weights: tuple[float, float, float],
                        order: str = "balancer", ranges: dict | None = None) -> list[Decision]:
    """n decisions from the stated ranges in SYNTH_RANGES (a fallback when the released labels are
    not downloaded, and the generator the tests use)."""
    r = {**SYNTH_RANGES, **(ranges or {})}
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        reqs = [_synthetic_request(rng, r) for _ in range(rng.randint(*r["round_size"]))]
        req = reqs[0]
        states = tuple(_synthetic_state(rng, r, sum(req.length) / len(req.length))
                       for _ in INSTANCE_TIER)
        lats = [latency_s(TIERS[k], s, req.length[k], req.prompt_tokens) for k, s in zip(INSTANCE_TIER, states)]
        l_run = max(max(lats), max(lats) * rng.uniform(*r["l_run_factor"]))
        out.append(Decision(req, states, round_scales(reqs, l_run), weights, order))
    return out


# ---------------------------------------------------------------- index-probe, box mode

EXAMPLE = synthetic_decisions(1, seed=15, weights=PRESETS["80/10/10"])[0]


def phi(point) -> float | None:
    """index-probe box mode: `python <repo>/tools/index-probe/index_probe.py box --objective
    model:phi --box example_box.json` from this directory (one stated synthetic decision)."""
    return EXAMPLE.phi(tuple(point))


def write_example_box(path: Path = HERE / "example_box.json") -> Path:
    path.write_text(json.dumps({"values": EXAMPLE.values, "anchor": list(EXAMPLE.anchor)}), encoding="utf-8")
    return path


if __name__ == "__main__":
    print(write_example_box())
