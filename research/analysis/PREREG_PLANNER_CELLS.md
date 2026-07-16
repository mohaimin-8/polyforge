# Pre-registration: planning-cell partitioning to 1024 tenants (Wave 4, local)

Registered 2026-07-16 (session 21). Committed and pushed **before** the run
together with its script `planner_cells.py`; the git push is the timestamp
anchor; to be mirrored to OSF prospectively per DEFENSE_QA #17.

## Question

`PLANNER_SCALING.md` measured the monolithic joint planner's cycle latency
growing super-linearly (fitted exponent 1.45; p95 crosses the 3 s operator
timeout at 128 tenants, the 10 s control period at 256) and named the
mitigation as future work: "partition the portfolio into planning cells."
Wave 4 executes that named lever. Two things must be measured together, or
the result is meaningless: (1) does partitioning restore deadline-feasibility
at 1024 tenants, and (2) what does independent-cell planning cost in *global*
fairness, since the joint fairness term no longer couples tenants across
cells?

This is a local, CPU-only experiment on the deployed planner code
(`services/planner/planner.PlannerCore.plan`, the exact path the planner
container runs, HTTP excluded). It does not touch the live cluster (Wave 3/4
live items remain deferred) and changes no production planner code — the
partitioner is a harness-level wrapper that calls the real `PlannerCore.plan`
once per cell, which is exactly how a partitioned deployment would scale out
planner replicas.

## Design (frozen)

- **Portfolio sizes** N ∈ {8, 32, 64, 128, 256, 512, 1024}.
- **Demand (frozen, heterogeneous so global Jain is a real quantity < 1):**
  ai_cacheable-shaped per tenant (chat 4 / embed 3 / crud_read 5 rps × a
  seeded log-normal-ish jitter 1 ± 0.2), and **every 8th tenant is a whale**
  — 4× demand, budget 12 USD — mirroring the sim's whale-mix ratio. Seed 42,
  `numpy.random.default_rng`, identical draw fed to both arms at each N.
- **Cell size** K = 32 (one planning cell ≈ a small cluster). Number of
  cells C = ceil(N/K).
- **Partition rule (frozen): round-robin** — tenant at sorted index i goes to
  cell (i mod C). Round-robin spreads whales evenly across cells so no cell
  is systematically starved; contiguous-by-budget partitioning (all whales in
  one cell) is the adversarial opposite and is named as future work, not
  measured here.
- **Per-cell cluster limits (frozen, capacity-parity):** a cell of k tenants
  gets replicas = 3·k, cache_mb = 256·k — the same per-tenant capacity the
  monolithic arm gets (3·N / 256·N), so no arm is capacity-advantaged.
- **Latency:** per N, one warm-up then timed calls — 20 calls for N ≤ 128,
  10 for N = 256, 5 for N = 512, 3 for N = 1024 (frozen; large-N monolithic
  calls are ~90 s each, so the count is bounded while the median stays
  stable). Monolithic latency = the single plan() call. Partitioned latency
  per round = **max over cells** of that cell's plan() time (the parallel
  deployment latency: cells run on independent planner replicas); the
  **sum over cells** (single-planner serial cost) is reported beside it.
- **Global objective metrics:** from one warm plan per arm, compute over all
  N tenants using the planner's own projected outcomes: global
  Jain = jain_index([1 − projected_violation_i]) (`model.jain_index`, the
  scoring the controller optimizes), mean projected violation, mean projected
  cost. Single-thread, this machine; absolute latencies indicative, the
  *shape* and the *cross-arm gap* are the claim.

## Hypotheses (frozen)

- **PS-H1 (primary, feasibility):** partitioned per-cell p95 latency stays
  **below the 3 s operator timeout at every N including 1024**, while the
  monolithic p95 exceeds 3 s at some N ≤ 1024. (Monolithic is already known
  to cross at 128; the test is that partitioning fixes it.)
- **PS-H2 (primary, fairness cost):** global Jain under partitioning is
  within **ΔJain ≤ 0.05** of the monolithic plan at every N. The actual gap
  is reported at every N; a gap > 0.05 is the measured tradeoff, published as
  such — it would say partitioning buys scale at a stated fairness price, not
  that the method failed.
- **PS-H3 (descriptive):** fitted latency growth exponent in N for each arm
  (monolithic expected ≈ 1.45 reproduced; partitioned per-cell expected ≈ 0,
  i.e. flat, since cell size is fixed).

## Outcome handling and stopping rule

Results as measured to `PLANNER_CELLS.md`, including a PS-H1 or PS-H2 failure
verbatim. One execution; N grid, cell size, partition rule, demand seed, and
call counts are frozen by this push and may not change after seeing any
number. No second cell size swept in this campaign (K = 32 is the registered
choice; a K sweep would be a new pre-registration). Coordination overheads
outside the planner (CR write fan-out, telemetry aggregation) are still not
measured and remain named in DEFENSE_QA #13.
