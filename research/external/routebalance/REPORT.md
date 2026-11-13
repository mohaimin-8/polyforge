# RouteBalance through the interaction index (WP15): what was done and what was simplified

Exploratory, out-of-sample check of the index on a published system that found fusion to pay:
W. Da and E. Kalyvianaki, "RouteBalance: Fused Model Routing and Load Balancing for Heterogeneous
LLM Serving", arXiv 2606.17949v2 (31 Oct 2026); their code and data at github.com/AKafakA/route-balance,
branch `submission`, commit ac49feb. Their repository was downloaded into `upstream/` (git-ignored)
and **read as data only: none of their code was installed or run**. Numbers: `RESULTS.md`.

## The decision

A request of a scheduling round chooses one of the fleet's 13 instances, which fixes the model (four
Qwen sizes). RouteBalance maximises S = w_q Q + w_c C + w_l T over the request's candidates (paper Eq.
2-3, their `routebalance/schedule/score.py:97-101`), with the latency estimate of paper Eq. 1 (their
`routebalance/estimate/latency.py`). `rb_model.py` minimises phi = 1 - S.

## Simplifications (each one a place where this is not their system)

1. **The box.** The fused choice among 13 (model, instance) pairs is not a product of two lists; the
   index needs one, so the instance is written as its rank within its model (knob 2 = rank 0..4 in the
   balancer's own order: fewest engine-reported requests, then least recently assigned, then fleet
   order). Pairs with no instance at that rank are infeasible points. A sensitivity variant ranks by
   the fused latency estimate (`RESULTS.md`, section 6).
2. **The anchor** is RB-decoupled (paper Sec. 4): the router scores the four models on idle-instance
   latency, then least-outstanding places the request on that model's rank-0 instance. Our per-knob
   choice (each knob alone, the other at the anchor) and their sequential decoupled design (router
   first, balancer second) are reported separately.
3. **Quality and length estimates are a surrogate.** Their k-NN estimator needs MiniLM embeddings that
   are not released; each label is shrunk towards its dataset's training mean with spreads fitted in
   `analyse.py` to released statistics of their runs (`RESULTS.md`, section 1).
4. **The fleet is simulated, not run** (`fleet.py`): their trace algorithm (send order, gamma arrivals)
   and round rule re-implemented from their description; telemetry by dead reckoning as theirs, with
   our own engine-count timing; an instance serves every request at once (no KV-cache limit), first
   token after a fitted prefill line, then one token per TPOT(b) on their measured curve, extended
   linearly past its last point.
5. **One seed.** Their measured reductions (E2E latency and cost against RB-decoupled) are from their
   seed-1 runs; the out-of-sample test correlates 48 preset x rate cells.

## What it found (details in `RESULTS.md`)

- Theorem 1 held on all 168,481 decisions; `tools/index-probe` reproduced the index exactly on 24,240.
- The index is large on their decision (5.7% to 34.9% of phi at the fused optimum, by preset), as large
  as our high-index regimes, and the per-knob one-step loss (0.2% to 4.0% of phi) is 4 to 30 times
  ours at the slow knobs, which fits their much larger measured gains.
- Across their 48 cells the index ranks with their measured gains only weakly (Spearman +0.36 E2E,
  +0.32 cost); the model's own run-level cuts track their measurements closely (+0.92, +0.89).

## Not done

- `NOTES.md`, a line-by-line map of every equation and constant to the paper and their code, was not
  written; the module docstrings carry the source references.
- No unit tests of `rb_model.py` and `fleet.py` beyond the Theorem 1 and index-probe checks inside
  `analyse.py`.
- Their own traces were not replayed through their own code (never run).
