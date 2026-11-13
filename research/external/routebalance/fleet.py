"""Decision instances from RouteBalance's released inputs: a small fleet simulation (WP15).

model.py is one decision; this module produces the states those decisions are taken in. It reads,
as data, files of the authors' repository (branch `submission`, ac49feb) from upstream/ (git-ignored,
see README.md); it never imports or runs their code.

  data/labels/qwen_eval.jsonl, qwen_train.jsonl   per prompt: dataset, prompt tokens (input_len), and
                                                 every model's judged quality and output length
  data/prompt_files/qwen_eval_prompts_3532_20260928.jsonl   the dataset rows, in file order

What is reproduced from their description, and what is ours (REPORT.md lists every simplification):
  trace      their algorithm re-implemented: send order random.Random(seed - 1).shuffle of the rows
             (bench/dataset.py:75-87), arrivals RandomState(seed) gamma(shape 1) gaps rescaled so the
             last lands at n / rate (bench/arrivals.py:51-74). Same seed, same algorithm.
  estimates  OURS: a surrogate of their k-NN estimator (MiniLM embeddings are not released): each
             label is shrunk towards its dataset's training mean, q = mu_d + a common + s specific,
             L = muL_d + s_len (L - muL_d); s, s_len and a are fitted in analyse.py to released
             statistics of their runs.
  rounds     their rule (routebalance/schedule/rounds.py): open on the first waiting request, take up to
             max(4, min(backlog, 32)), close when full or 50 ms after opening; then the batched
             estimate and one load read (durations from paper Table 6), then the round's decisions.
  telemetry  dead reckoning as theirs (telemetry.py): running = min(engine gauge, in flight), waiting =
             the rest; pending decode tokens from progress with the +10 extension. OURS: the engine
             counts a request one decode step (plus 5 ms) after dispatch and stops one step after it
             ends; the balancer reads that count from a 50 ms poll.
  plant      OURS: an instance serves every request at once (no KV-cache limit); first token after the
             fitted prefill line; then one token per TPOT(b), b = requests on the instance, on their
             measured curve (data/tpot_qwen.json), extended linearly past its last point.
"""

from __future__ import annotations

import heapq
import json
import math
import random
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:      # run with python -I (isolated): only this directory, never upstream/
    sys.path.insert(0, str(HERE))

import rb_model as M  # noqa: E402
UPSTREAM = HERE / "upstream" / "route-balance-ac49feb0e46662223ed6cb0b638bf2bbe705deb2"
JUDGE = "deepeval-llama3.1-8b-it_reference"
MAX_TOKENS = 1024             # paper Sec. 5.1: at most 1,024 output tokens
ROUND_SIZE, ROUND_TIMEOUT_S, BACKLOG_CAP = 4, 0.050, 32   # rounds.py:31-32 (busy ratio below 0.3)
POLL_S = 0.050                # the balancers' cache period (paper Sec. 4)
NET_S = 0.005                 # OURS: dispatch to the engine
SCHEDULE_S = 0.001            # paper Table 6: scheduling takes at most 1.2 ms a round
# Paper Table 6 (tab:overhead): rate -> (estimate ms, load read ms), at RouteBalance 80/10/10.
TABLE6 = {12: (47, 22), 18: (60, 24), 24: (69, 24), 30: (83, 24), 36: (96, 28), 42: (99, 28)}
# Their mean round size at those rates (data/round_range.json, seed 1), used to turn the estimate
# time into a line in the round size.
LOGGED_ROUND = {12: 1.825, 18: 2.436, 24: 3.077, 30: 3.894, 36: 4.954, 42: 5.660}
# RB-decoupled's mean time before dispatch, ms (data/qwen_report.json, qwen.rb-static-dec.q80l10c10
# at seed 1, summary.off_instance_ms.mean): the router's wait and estimate, per rate.
ROUTER_MS = {12: 52.4, 18: 67.2, 24: 80.6, 30: 112.9, 36: 155.9, 42: 188.7}
# Measured TPOT curves, ms per token at b running (data/tpot_qwen.json "curve"), fleet tier order.
TPOT_CURVE = (
    ((1, 11.81388760868277), (4, 15.218658764071163), (16, 18.943275874148874), (32, 23.582751549368883),
     (64, 34.883984363849095), (96, 46.809054156011015)),
    ((1, 9.625211706933207), (4, 9.820593911067323), (16, 10.81984084093358), (32, 12.031080487017391),
     (64, 15.309347534885474), (96, 19.698639261654833)),
    ((1, 43.55641618929546), (4, 44.59376508277095), (16, 44.59376508277095), (32, 44.59376508277095),
     (64, 50.63072531615716)),
    ((1, 18.479830013818518), (4, 19.341745158376874), (16, 21.060163436762654), (32, 22.858083675684966),
     (64, 28.12069261671568), (96, 36.181831839471556)),
)


def _interp(table: dict, rate: float) -> float:
    """A scalar table over the rate, linear between its rates and clamped at its ends."""
    keys = sorted(table)
    return float(np.interp(rate, keys, [table[k] for k in keys]))


def load_read_s(rate: float) -> float:
    return _interp({r: v[1] for r, v in TABLE6.items()}, rate) / 1000.0


def estimate_line() -> tuple[float, float]:
    """Estimate time (ms) = alpha + beta * round size, least squares over Table 6 and the logged sizes."""
    x = np.array([LOGGED_ROUND[r] for r in sorted(TABLE6)])
    y = np.array([TABLE6[r][0] for r in sorted(TABLE6)], float)
    beta, alpha = np.polyfit(x, y, 1)
    return float(alpha), float(beta)


def tpot_s(tier: int, b: int) -> float:
    """Plant TPOT at batch b: their curve, linear between points, extended linearly beyond the last."""
    pts = TPOT_CURVE[tier]
    if b <= pts[0][0]:
        return pts[0][1] / 1000.0
    for (b0, m0), (b1, m1) in zip(pts, pts[1:]):
        if b <= b1:
            return (m0 + (m1 - m0) * (b - b0) / (b1 - b0)) / 1000.0
    (b0, m0), (b1, m1) = pts[-2], pts[-1]
    return (m1 + (m1 - m0) * (b - b1) / (b1 - b0)) / 1000.0


# ---------------------------------------------------------------- their released inputs

@dataclass(frozen=True)
class Labels:
    ids: tuple[str, ...]          # evaluation rows in prompt-file order
    dataset: np.ndarray           # per row
    prompt_tokens: np.ndarray     # input_len
    quality: np.ndarray           # rows x models, judged label
    length: np.ndarray            # rows x models, output tokens
    mu_q: np.ndarray              # rows x models: the row's dataset mean over the training labels
    mu_len: np.ndarray


def _read_labels(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_labels(root: Path = UPSTREAM) -> Labels:
    train = _read_labels(root / "data" / "labels" / "qwen_train.jsonl")
    ev = {d["request_id"]: d for d in _read_labels(root / "data" / "labels" / "qwen_eval.jsonl")}
    with (root / "data" / "prompt_files" / "qwen_eval_prompts_3532_20260928.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    ids = tuple(r["id"] for r in rows if "id" in r)          # the first line is the file's header
    if len(ids) != len(ev) or set(ids) != set(ev):
        raise ValueError("the prompt file and the evaluation labels disagree")

    def q_of(d):
        return [d["models"][m]["llm_judge_scores"][JUDGE] for m in M.MODELS]

    def len_of(d):
        return [d["models"][m]["output_length"] for m in M.MODELS]

    tq = {}
    for d in train:
        tq.setdefault(d["dataset"], []).append((q_of(d), len_of(d)))
    mu = {k: (np.mean([a for a, _ in v], 0), np.mean([b for _, b in v], 0)) for k, v in tq.items()}
    dsets = np.array([ev[i]["dataset"] for i in ids])
    return Labels(ids, dsets, np.array([ev[i]["input_len"] for i in ids]),
                  np.array([q_of(ev[i]) for i in ids], float), np.array([len_of(ev[i]) for i in ids], float),
                  np.array([mu[d][0] for d in dsets]), np.array([mu[d][1] for d in dsets]))


@dataclass(frozen=True)
class Surrogate:
    s: float          # share of each label's model-specific deviation that the estimate keeps
    s_len: float      # share of each output length's deviation from its dataset mean
    a: float          # share of the prompt's common deviation (difficulty) the estimate keeps

    def estimates(self, lab: Labels) -> tuple[np.ndarray, np.ndarray]:
        dev = lab.quality - lab.mu_q
        common = dev.mean(1, keepdims=True)
        q = lab.mu_q + self.a * common + self.s * (dev - common)
        length = lab.mu_len + self.s_len * (lab.length - lab.mu_len)
        return q, length


def send_order(n_rows: int, seed: int) -> list[int]:
    rows = list(range(n_rows))
    random.Random(seed - 1).shuffle(rows)                    # per_seed order (bench/dataset.py:75-87)
    return rows


def arrival_times(n: int, rate: float, seed: int) -> np.ndarray:
    stream = np.random.RandomState(seed)                     # bench/arrivals.py:51-74, shape 1
    cum = np.cumsum([stream.gamma(shape=1.0, scale=1.0 / rate) for _ in range(n)])
    return cum * ((n / rate) / cum[-1])


def round_partition(arrivals: np.ndarray, rate: float) -> list[tuple[int, int, float]]:
    """Rounds as (first, end, decision time); depends on arrivals and timing only, not on decisions."""
    alpha, beta = estimate_line()
    load_s = load_read_s(rate)
    out, free, nxt, n = [], 0.0, 0, len(arrivals)
    while nxt < n:
        t_open = max(free, arrivals[nxt])
        backlog = int(np.searchsorted(arrivals, t_open, side="right")) - nxt
        limit = max(ROUND_SIZE, min(backlog, BACKLOG_CAP))
        deadline = t_open + ROUND_TIMEOUT_S
        end = nxt
        while end < n and end - nxt < limit and arrivals[end] <= deadline:
            end += 1
        t_close = max(t_open, arrivals[end - 1]) if end - nxt == limit else deadline
        t_read = t_close + (alpha + beta * (end - nxt)) / 1000.0 + load_s
        out.append((nxt, end, t_read))
        free, nxt = t_read + SCHEDULE_S, end
    return out


# ---------------------------------------------------------------- the plant

@dataclass
class _Job:
    rid: int
    tier: int
    t_arrive: float
    t_disp: float
    t_vis: float          # the engine's gauge counts it from here
    t_ft: float           # first token
    length: int           # actual output tokens (their label)
    pred: int             # predicted decode tokens the scheduler tracks
    v_start: float | None = None
    t_done: float | None = None


@dataclass
class _Instance:
    tier: int
    active: dict = field(default_factory=dict)
    decoding: list = field(default_factory=list)   # heap of (virtual end, rid)
    recent: deque = field(default_factory=deque)   # completed jobs, kept 0.3 s
    v: float = 0.0                                 # tokens produced per decoding job so far
    t: float = 0.0
    version: int = 0

    def tpot(self) -> float:
        return tpot_s(self.tier, max(1, len(self.active)))

    def advance(self, t: float) -> None:
        if self.active:
            self.v += (t - self.t) / self.tpot()
        self.t = t

    def v_at(self, t: float) -> float:
        return self.v + ((t - self.t) / self.tpot() if self.active else 0.0)


class Plant:
    def __init__(self):
        self.insts = [_Instance(k) for k in M.INSTANCE_TIER]
        self.events: list = []
        self.seq = 0
        self.done: list[_Job] = []

    def _push(self, t, kind, i, payload):
        self.seq += 1
        heapq.heappush(self.events, (t, self.seq, kind, i, payload))

    def _reschedule(self, i: int, t: float) -> None:
        inst = self.insts[i]
        inst.version += 1
        if inst.decoding:
            self._push(t + max(0.0, inst.decoding[0][0] - inst.v) * inst.tpot(), "done", i, inst.version)

    def dispatch(self, job: _Job, i: int) -> None:
        inst = self.insts[i]
        inst.advance(job.t_disp)
        inst.active[job.rid] = job
        self._reschedule(i, job.t_disp)
        self._push(job.t_ft, "ft", i, job.rid)

    def run_until(self, t_end: float) -> None:
        while self.events and self.events[0][0] <= t_end:
            t, _, kind, i, payload = heapq.heappop(self.events)
            inst = self.insts[i]
            if kind == "ft":
                job = inst.active[payload]
                inst.advance(t)
                job.v_start = inst.v
                heapq.heappush(inst.decoding, (inst.v + max(0, job.length - 1), job.rid))
                self._reschedule(i, t)
            elif payload == inst.version:
                inst.advance(t)
                while inst.decoding and inst.decoding[0][0] <= inst.v + 1e-9:
                    _, rid = heapq.heappop(inst.decoding)
                    job = inst.active.pop(rid)
                    job.t_done = t
                    inst.recent.append(job)
                    self.done.append(job)
                inst.advance(t)        # the rate changes from here
                self._reschedule(i, t)
        for inst in self.insts:
            while inst.recent and inst.recent[0].t_done < t_end - 0.3:
                inst.recent.popleft()

    def engine_count(self, i: int, u: float) -> int:
        """Requests the engine reports (running + waiting) at time u."""
        inst = self.insts[i]
        step = M.TIERS[inst.tier].tpot_s
        live = sum(1 for j in inst.active.values() if j.t_vis <= u)
        return live + sum(1 for j in inst.recent if j.t_vis <= u < j.t_done + step)

    def pending_tokens(self, i: int, t: float) -> int:
        """Pending decode tokens with streamed progress and the +10 extension (telemetry.py)."""
        inst = self.insts[i]
        v = inst.v_at(t)
        total = 0
        for j in inst.active.values():
            produced = 0 if j.v_start is None or t < j.t_ft else min(j.length, 1 + int(v - j.v_start))
            total += j.pred - produced if produced < j.pred else M.AUTO_EXTEND_TOKENS - (produced - j.pred) % 10
        return total

    def rb_state(self, i: int, t_read: float, t_gauge: float, last: float) -> M.InstanceState:
        in_flight = len(self.insts[i].active)
        running = min(self.engine_count(i, t_gauge), in_flight)
        poll = math.floor(t_read / POLL_S) * POLL_S
        return M.InstanceState(running, in_flight - running, self.pending_tokens(i, t_read),
                               self.engine_count(i, poll), last)


# ---------------------------------------------------------------- schedulers

POLICIES = ("fused", "reqnorm", "sequential", "decoupled")


@dataclass(frozen=True)
class SimConfig:
    rate: float
    weights: tuple[float, float, float]
    policy: str
    seed: int = 1
    order: str = "balancer"
    log_decisions: bool = False


@dataclass
class SimResult:
    config: SimConfig
    decisions: list           # model.Decision per logged decision (round-based policies)
    outcomes: dict


def _job(rid, k, tier, t_arr, t_disp, lab, est_len):
    t_obj = M.TIERS[tier]
    return _Job(rid, tier, t_arr, t_disp, t_disp + NET_S + t_obj.tpot_s,
                t_disp + t_obj.prefill_a_s + t_obj.prefill_b_s * int(lab.prompt_tokens[k]),
                int(lab.length[k, tier]), M.own_tokens(est_len[k, tier]))


def _request(k, lab, q, length) -> M.Request:
    return M.Request(int(lab.prompt_tokens[k]), tuple(float(x) for x in q[k]), tuple(float(x) for x in length[k]))


def _run_rounds(cfg, lab, q, length, order, arr, plant):
    decisions, last, rounds = [], {}, []
    for first, end, t_read in round_partition(arr, cfg.rate):
        plant.run_until(t_read)
        members = list(range(first, end))
        reqs = {p: _request(order[p], lab, q, length) for p in members}
        lpt = sorted(members, key=lambda p: max(reqs[p].length), reverse=True)
        load_s = load_read_s(cfg.rate)
        states = [plant.rb_state(i, t_read, t_read - 0.5 * load_s, last.get(i, 0.0))
                  for i in range(len(M.INSTANCE_TIER))]
        l_run, given = 0.0, set()
        rounds.append({"size": len(members), "range": float(q[[order[p] for p in members]].max()
                                                             - q[[order[p] for p in members]].min())})
        for pos, p in enumerate(lpt):
            req = reqs[p]
            lats = [M.latency_s(M.TIERS[k], s, req.length[k], req.prompt_tokens) for k, s in zip(M.INSTANCE_TIER, states)]
            l_run = max(l_run, max(lats))
            # the policy's own objective: per-request scales for the ablation, the round's otherwise
            scales = (M.request_scales(req, lats) if cfg.policy == "reqnorm"
                      else M.round_scales(list(reqs.values()), l_run))
            d = M.Decision(req, tuple(states), scales, cfg.weights, cfg.order)
            i = d.instance(d.sequential()) if cfg.policy == "sequential" else d.fused_instance()
            if cfg.log_decisions:
                decisions.append(d)
            k = order[p]
            t_disp = t_read + SCHEDULE_S
            plant.dispatch(_job(k, k, M.INSTANCE_TIER[i], arr[p], t_disp, lab, length), i)
            s = states[i]
            stamp = t_read + 1e-6 * (pos + 1)
            states[i] = M.InstanceState(s.running, s.waiting + 1, s.pending + M.own_tokens(req.length[M.INSTANCE_TIER[i]]),
                                        s.lor_count, stamp)
            last[i] = stamp
            rounds[-1].setdefault("repeat", 0)
            rounds[-1]["repeat"] += i in given
            given.add(i)
    return decisions, rounds


def _run_pipeline(cfg, lab, q, length, order, arr, plant):
    router_s = _interp(ROUTER_MS, cfg.rate) / 1000.0
    last = {}
    for p, k in enumerate(order):
        t = arr[p] + router_s
        plant.run_until(t)
        req = _request(k, lab, q, length)
        tier = M.decoupled_model(req, cfg.weights)
        poll = math.floor(t / POLL_S) * POLL_S
        cands = [i for i, tt in enumerate(M.INSTANCE_TIER) if tt == tier]
        i = min(cands, key=lambda c: (plant.engine_count(c, poll), last.get(c, 0.0), c))
        last[i] = t
        plant.dispatch(_job(k, k, tier, arr[p], t, lab, length), i)
    return [], []


def simulate(cfg: SimConfig, lab: Labels, sur: Surrogate) -> SimResult:
    if cfg.policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    q, length = sur.estimates(lab)
    order = send_order(len(lab.ids), cfg.seed)
    arr = arrival_times(len(order), cfg.rate, cfg.seed)
    plant = Plant()
    run = _run_pipeline if cfg.policy == "decoupled" else _run_rounds
    decisions, rounds = run(cfg, lab, q, length, order, arr, plant)
    plant.run_until(math.inf)
    jobs = sorted(plant.done, key=lambda j: j.rid)
    if len(jobs) != len(order):
        raise RuntimeError(f"{len(jobs)} of {len(order)} requests completed")
    tiers = np.array([j.tier for j in jobs])
    ks = np.array([j.rid for j in jobs])
    cost = lab.prompt_tokens[ks] * np.array([M.TIERS[t].cost_in for t in tiers]) \
        + lab.length[ks, tiers] * np.array([M.TIERS[t].cost_out for t in tiers])
    outcomes = {
        "e2e_mean_s": float(np.mean([j.t_done - j.t_arrive for j in jobs])),
        "before_dispatch_ms": 1000.0 * float(np.mean([j.t_disp - j.t_arrive for j in jobs])),
        "quality": float(lab.quality[ks, tiers].mean()),
        "cost_per_million_usd": float(cost.mean() * 1e6),
        "mix": [float((tiers == t).mean()) for t in range(len(M.TIERS))],
    }
    if rounds:
        outcomes.update(rounds=len(rounds), mean_round_size=float(np.mean([r["size"] for r in rounds])),
                        round_range=float(np.mean([r["range"] for r in rounds])),
                        repeat_share=float(sum(r.get("repeat", 0) for r in rounds) / len(order)))
    return SimResult(cfg, decisions, outcomes)
