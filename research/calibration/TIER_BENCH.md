# Phase 6 calibration — GPU path (tier table), as measured

Measured 2026-07-12 on a free Kaggle GPU kernel
(`mohaimin08/polyforge-tier-bench`, version 6; script committed as
`kaggle_tier_bench.py`, raw output committed as `tier_bench.csv`).
Protocol: fixed 48-token completions (same request shape as the CPU-path
congestion calibration), 3 warmups then n=25 sequential runs per tier,
greedy decoding, transformers with `device_map="auto"`.

| tier | model | n | mean ms | p95 ms | tokens/s |
|---|---|---|---|---|---|
| small | Qwen2.5-0.5B-Instruct | 25 | 1241.9 | 1282.7 | 38.65 |
| mid | Qwen2.5-3B-Instruct | 25 | 1882.8 | 1923.0 | 25.49 |
| large | Qwen2.5-7B-Instruct | 25 | 20665.1 | 20738.0 | 2.32 |

## What this supports, and what it does not

- **Tier ordering confirmed.** The sim's `TIER_BASE_LATENCY_MS` asserts
  small < mid < large per request; measured means are monotone in model
  size. Measured mid/small = 1.52× vs the sim's chat-family 2.67× — same
  direction, flatter slope on identical hardware.
- **The `large` row is an upper bound, disclosed.** 7B fp16 (~15.4 GB
  weights) exceeded the granted card's free VRAM and `device_map="auto"`
  spilled layers to CPU — 2.32 tok/s is offload-dominated, not pure-GPU.
  The small/mid rows ran fully on-GPU. **SUPERSEDED session 44: re-run on
  GPU T4 x2 with zero modules offloaded, 20665.1 ms -> 3171.5 ms (6.52x).
  See "Three tiers on T4 x2" at the end of this file — the offload was an
  allocation artifact, and it was hiding the tier's real shape.**
- **Device identity was not captured**: Kaggle's API returned an empty log
  for the run. Circumstantially a single 16 GB pre-sm_70 card (P100 pool):
  versions 2–5 of this kernel failed with sm_60-typical torch errors, and
  the script's committed fallback (cu118 torch + pinned contemporaries,
  broken torchvision removed) is what made this run complete.
- **Price-shape note.** Measured GPU-seconds per request (small 1.24 s,
  mid 1.88 s) are far flatter than `TIER_COST_USD_PER_REQ`'s 1:10:100 —
  as expected: the sim's price table mirrors *market per-request pricing*
  (bigger models run on bigger, costlier fleets), not GPU-seconds on one
  fixed card. The table is a pricing assumption, not a hardware claim; no
  constant changes (adopting measured constants = new pre-registration,
  session 16b precedent).
- Single-stream, unloaded: p95/mean here is 1.00–1.03 by design (no
  queueing); the loaded-tail measurement is the CPU path's
  (`CALIBRATION.md`, p95/mean 1.59–2.41 under Poisson load).

Deliberate engine deviation from V2_README's "vLLM serving": transformers,
because Kaggle images ship it preinstalled and the free pool's GPU
architecture is not guaranteed vLLM-compatible. Single-stream shape is
engine-agnostic; serving-optimized absolutes would be lower for every tier
alike.

## Independent replication (2026-07-16, session 22, kernel version 7)

Re-ran the identical script on a fresh Kaggle GPU session
(`tier_bench_replication.csv`). The measurement **reproduces within ~3%** on
every tier:

| tier | mean ms (16d) | mean ms (v7) | Δ | tokens/s (16d) | tokens/s (v7) |
|---|---|---|---|---|---|
| small | 1241.9 | 1205.2 | −3.0% | 38.65 | 39.83 |
| mid | 1882.8 | 1887.0 | +0.2% | 25.49 | 25.44 |
| large | 20665.1 | 20594.9 | −0.3% | 2.32 | 2.33 |

What this establishes and what it does not: the tier-latency *ordering and
shape* (small < mid < large, super-linear at large) is **reproducible across
independent GPU sessions**, not a one-off. The `large` row's CPU-offload
caveat **persists** — the pool again granted a single ~16 GB card, and the
7B fp16 (~15 GB) still spills to CPU; the near-identical 2.33 tok/s is the
signature of the same offload path. Removing that caveat needs a 32 GB
allocation (Kaggle "GPU T4 ×2"). **That allocation IS selectable from the
`kaggle kernels push` script API — the claim that it is UI-gated was wrong
and was corrected in session 44** (`"machine_shape": "NvidiaTeslaT4"` in
`kernel-metadata.json`; see `WAVE4_FREE_ROUTE.md` 0b). The `large` caveat is
therefore a live follow-up, not a blocked one: 2 × 16 GB with
`device_map="auto"` should hold the 7B fp16 entirely on-GPU. It has not been
re-run yet — the batched bench covers `small` and `mid` only. The small/mid rows ran
fully on-GPU in both sessions and stand as clean measurements. No sim
constant changes (replication of an engineering microbenchmark, not a new
pre-registration).


---

# T4 ×2 batched re-bench (L3), as measured

Measured 2026-09-07 on `polyforge-tier-bench-batched` v2, the first run after
`machine_shape` was set to `NvidiaTeslaT4`. The kernel reported
`devices=2 / gpu0: Tesla T4 / gpu1: Tesla T4`. Full matrix in
`tier_bench_batched.csv` (18 rows: 16 cells + 2 exactness checks).

**The architecture failure is gone.** Session 42's run of this same script
failed all 16 cells with `cudaErrorNoKernelImageForDevice` on an sm_60 P100.
On sm_75 T4s, **all 16 cells produced numbers and the `error` column is empty
throughout**. Batched-vs-serial exactness is `True` for both tiers, so
batching does not perturb the outputs.

## Batching gain (48 new tokens)

| tier | batch | wall s | req/s | tokens/s |
|---|---|---|---|---|
| small | 1 | 1.5443 | 0.65 | 31.1 |
| small | 8 | 1.4206 | 5.63 | 270.3 |
| small | 32 | 1.5197 | 21.06 | 1010.7 |
| small | 64 | 1.6620 | 38.51 | 1848.4 |
| mid | 1 | 2.2727 | 0.44 | 21.1 |
| mid | 8 | 2.2690 | 3.53 | 169.2 |
| mid | 32 | 3.3717 | 9.49 | 455.6 |
| mid | 64 | 4.5806 | 13.97 | 670.7 |

`small` absorbs 64× the work for 1.08× the wall time; `mid` for 2.02×.

## Two things this run says that the headline numbers do not

**1. Per-card, the T4 is SLOWER than the P100 it replaced.** Against the
committed baseline above, at batch=1 / 48 tokens:

| tier | P100 tokens/s | T4 tokens/s | ratio |
|---|---|---|---|
| small | 38.65 | 31.1 | 0.80× |
| mid | 25.49 | 21.1 | 0.83× |

This is the expected direction, not an anomaly: single-stream decode is
memory-bandwidth-bound, and the P100's HBM2 (~732 GB/s) beats the T4's GDDR6
(~320 GB/s). The T4 wins on *throughput under batching*, not on latency. Any
claim of the form "moving to T4 ×2 made the route faster" is false at
batch=1 and must be stated as a batching result.

**2. Half the allocated hardware was idle.** The script loads with
`device_map="cuda:0"` (line 135), so **`gpu1` did no work for the entire
468 s run**. Every row above is a *one*-card number reported from a two-card
allocation, and that headroom is not reflected anywhere in
`tier_bench_batched.csv`.

**The obvious prediction — that two replicas would roughly double the req/s
column, the workload being embarrassingly parallel across requests — was
written here and then MEASURED, and it is wrong.** See "What the second T4 is
actually worth" below: the gain is **1.15× to 1.93×**, depending entirely on
how GPU-bound the cell is, and only one cell of eight approaches 2×.

## Defect: the prefill/decode split emits an impossible TTFT

The run's console diagnostic reported:

```
small TPOT  31.83 ms/token   TTFT    16.5 ms
mid   TPOT  47.50 ms/token   TTFT    -7.4 ms
```

A negative time-to-first-token is not physical. The estimator is two-point —
`TPOT = (t_96 - t_48) / 48`, then `TTFT = t_48 - 48 · TPOT` — so for `mid`,
`2.2727 - 48 × 0.047502 = -0.0074 s`. What it actually shows is that prefill
at batch=1 sits **below the noise floor of this two-point difference**, not
that it is negative. `small`'s +16.5 ms comes from the same estimator and is
no more trustworthy; it merely lands positive.

**Scope of the damage was limited:** TTFT/TPOT are printed to the log only and
are **not columns in `tier_bench_batched.csv`**, so no scored artifact ever
carried them.

**RESOLVED — see "Single-card absolutes and a measured TTFT" below.** A direct
measurement (timing a 1-token generation) gives `small` 37.1 ms and `mid`
54.5 ms. The comparison is instructive about *which half* of the two-point fit
failed:

| | derived (two-point) | measured (direct) | error |
|---|---|---|---|
| small TPOT | 31.83 ms/tok | 32.22 ms/tok | +1.2% |
| mid TPOT | 47.50 ms/tok | 48.00 ms/tok | +1.1% |
| small TTFT | 16.5 ms | 37.1 ms | −2.2× |
| mid TTFT | −7.4 ms | 54.5 ms | nonsense |

**TPOT was fine all along; only TTFT was wrong.** That is the expected failure
of a two-point linear fit — the slope is a difference of well-separated
quantities and is robust, while the intercept is what is left after
subtracting two nearly equal numbers, so it absorbs the entire error budget.
The lesson generalises past this bench: a derived intercept needs its own
measurement, not a sanity check on the slope.


---

# Three tiers on T4 ×2 — the `large` row was an artifact

Measured 2026-09-07, kernel `polyforge-tier-bench-t4`, script committed at
`kernels/polyforge-tier-bench-t4/`, raw output `tier_bench_t4.csv`.

Methodology is the committed `kaggle_tier_bench.py` unchanged — same TIERS,
same PROMPT, `N_PREDICT=48`, `WARMUP=3`, `RUNS=25`, greedy, fp16,
`device_map="auto"`, same p95 formula — verified by an automated parity check
before the push. The only edits are a placement report and the output
filename, so these rows are directly comparable to the P100 table at the top.
The original generator is untouched and still produces `tier_bench.csv`.

**The measurement that settles it.** The open question was never speed — an
offloaded model is slow, not broken — so the run reports where `accelerate`
actually placed each module:

```
cuda devices: 2 ['Tesla T4', 'Tesla T4']
small  placements: ['0', '1']   modules offloaded to cpu/disk: 0
mid    placements: ['0', '1']   modules offloaded to cpu/disk: 0
large  placements: ['0', '1']   modules offloaded to cpu/disk: 0
```

Zero modules on CPU or disk for any tier, `large` included. The 7B held
entirely in GPU memory across the two cards.

| tier | P100 mean ms | T4 ×2 mean ms | change | P100 tok/s | T4 ×2 tok/s |
|---|---|---|---|---|---|
| small | 1241.9 | 2089.8 | 0.59× | 38.65 | 22.97 |
| mid | 1882.8 | 3081.5 | 0.61× | 25.49 | 15.58 |
| large | 20665.1 | **3171.5** | **6.52× faster** | 2.32 | **15.13** |

## What changes: the tier *shape*, not any constant

Normalising each run to its own `small`:

| tier | P100 ratio | T4 ×2 ratio |
|---|---|---|
| small | 1.000 | 1.000 |
| mid | 1.516 | 1.475 |
| large | **16.640** | **1.518** |

The mid/small ratio reproduces almost exactly (1.516 → 1.475). The large/small
ratio **collapses from 16.6× to 1.5×**. The earlier table's "super-linear at
large" was not a property of the tier; it was the signature of layers sitting
in host memory. On hardware that holds the weights, `large` costs about what
`mid` costs (+2.9%).

**The ordering survives, the scale does not.** `TIER_BASE_LATENCY_MS` asserts
small < mid < large, and the measured means are still monotone
(2089.8 < 3081.5 < 3171.5). What can no longer be claimed from this bench is
a steep or super-linear tier cost curve on fixed hardware.

**No sim constant changes**, for the same reason as the original table:
`TIER_COST_USD_PER_REQ`'s 1:10:100 mirrors *market per-request pricing*, not
GPU-seconds on one card, and adopting measured constants would need a new
pre-registration (session 16b precedent). This run makes the published gap
between the price table and the hardware *wider*, and that gap was already
disclosed.

## Caveat: every tier was sharded, including the two that did not need it

`device_map="auto"` split all three models across both cards. `small` and
`mid` fit on one T4 and gain nothing from the split — they only pay the
cross-GPU transfer on each forward pass. Against the single-card figures from
`polyforge-tier-bench-batched` (`device_map="cuda:0"`, batch=1, 48 tokens):

| tier | 1 × T4 tok/s | 2 × T4 sharded tok/s | cost of sharding |
|---|---|---|---|
| small | 30.94 | 22.97 | −25.8% |
| mid | 20.78 | 15.58 | −25.0% |

(Single-card column from `tier_bench_1gpu.csv`, a purpose-built run under this
file's protocol. The batched bench's independent batch=1 cells give 31.1 and
21.1 — agreement to 0.5% and 1.5% from a separately written script, so the
penalty is a property of the placement, not of one harness.)

So the `small` and `mid` absolutes above are **depressed by roughly a
quarter** and are not the best numbers this hardware can produce; the
single-card rows are. This does not weaken the `large` result or the ratio
comparison — all three tiers ran under one identical configuration, which is
what makes the ratio column apples-to-apples — but a table quoting best
achievable per-tier latency would place `small`/`mid` on one card and shard
only `large`.

## The corrected ratio reaches a frozen analysis grid, not just prose

**This is the most serious downstream consequence and it needs the author.**

`research/analysis/breakeven_tier.py` freezes a 60-vector price grid,
pre-registered in `PREREG_TIER_RATIO.md` and marked *"do not extend after
execution"*:

```python
R_MID   = [1.52, 3.0, 5.0, 10.0]
R_LARGE = [5.0, 10.0, 16.6, 33.0, 100.0]
GPU_CORNER = (1.52, 16.6, 1.0)
```

`GPU_CORNER` is `(1.52, 16.6)` — taken from the P100 table. **That is the
CPU-offload corner, not the GPU corner.** The measured ratio on hardware that
holds the weights is `(1.475, 1.518)`.

Both coordinates fall outside the frozen grid:

| | frozen grid | corrected measurement | inside grid? |
|---|---|---|---|
| `r_mid` | min 1.52 | 1.475 | **no** (3% below) |
| `r_large` | min 5.0 | **1.518** | **no** (3.3× below the smallest value) |

So the sensitivity analysis **never evaluated the actual hardware ratio**, and
by its own frozen-grid rule it cannot simply be re-run to cover it.

**What still stands, precisely.** The grid was validly frozen and validly
executed; the analysis did exactly what it declared. "No aggregate cost win
reversed anywhere in the grid" remains true *of that grid*. The *published*
corner `(10, 100)` is the market-pricing assumption and is untouched. No sim
constant changes.

**What does not stand.** The label. `(1.52, 16.6)` describes a card spilling
layers to host memory, not a tier price shape, so the analysis's claim to have
tested "the GPU corner" is no longer accurate. Two gated records carry the
figure in their titles — `RESULTS_TIER_RATIO.md` ("GPU corner 1:1.516:16.64")
and `RESULTS_TIER_WU.md` ("mid 1.516×, large 16.64×") — and
`PHASE7_ORDINAL.md` and `RESULTS_TRACE_LIVE.md` both cite this file for the
realism of the tier knob.

**Which way it moves the result is NOT known and is not guessed here.**
`r_large = 1.518` prices large-tier requests ~11× cheaper relative to small
than the tested corner did; whether that flips any comparison depends on the
per-arm tier mix. Establishing it means running outside a frozen grid, which
is a pre-registration decision, not an implementation one.

**Two defensible routes, both the author's call:** disclose that the corner
was mis-labelled and that the declared grid does not cover the corrected value
(the cheaper, honest option, and the existing result survives as stated); or
pre-register a new grid extending `r_large` downward and run it fresh.
**Nothing here has been changed or re-run.**

## Downstream text that also quotes the superseded number

`DEFENSE_QA.md` §15 states the GPU bench measured **1:1.516:16.64**. That
remains a true description of the P100 run and is disclosed there as
offload-dominated, but the clean measurement is **1:1.475:1.518**. The
argument §15 makes is unaffected in direction — it observes that measured
ratios are far flatter than the 1:10:100 price table, and the corrected
number is flatter still — but the figure itself should be updated before it
is quoted in a defense. Same for the `large`-row framing in `V2_README.md`
and `README.md`. **Author's call; not changed here.**


---

# Single-card absolutes and a measured TTFT

Measured 2026-09-07, kernel `polyforge-tier-bench-1gpu`, script at
`kernels/polyforge-tier-bench-1gpu/`, raw output `tier_bench_1gpu.csv`.
Protocol is this file's throughout — same PROMPT, `N_PREDICT=48`, `WARMUP=3`,
`RUNS=25`, greedy, fp16, same p95 formula, parity-checked and AST-parsed
before push. Two deliberate changes, each fixing a defect the T4 ×2 run
exposed.

| tier | mean ms | p95 ms | tokens/s | TTFT ms | TPOT ms/token |
|---|---|---|---|---|---|
| small | 1551.3 | 1595.1 | 30.94 | 37.1 | 32.22 |
| mid | 2310.2 | 2363.8 | 20.78 | 54.5 | 48.00 |

`large` is absent by design: it needs both cards, so it has no single-card
row to give. Its numbers stay in the T4 ×2 table above.

**A1 — `device_map={"": 0}` instead of `"auto"`.** These are the tiers that fit
on one T4 and were being split across two for nothing. Recovering the 25% the
split cost, these are the **best-achievable single-stream absolutes** on this
hardware, and the rows a per-tier latency table should quote.

**A2 — TTFT measured, not extrapolated.** Timing a 1-token generation measures
prefill plus one decode step outright; TPOT is then the marginal cost of the
remaining 47. Neither can be driven negative by noise, and both are now
**columns in the CSV** rather than a log line. TTFT/TPOT lands at 1.15× for
`small` and 1.14× for `mid` — a consistent ratio across two model sizes,
which the derived figures (0.52× and negative) did not produce.

## Where each tier row should now be read from

| tier | best single-stream source | why |
|---|---|---|
| small | `tier_bench_1gpu.csv` | fits one card; sharding is pure loss |
| mid | `tier_bench_1gpu.csv` | same |
| large | `tier_bench_t4.csv` | needs both cards to avoid CPU offload |

The `tier_bench_t4.csv` rows remain the correct source for the **ratio**
comparison, because all three tiers ran there under one identical
configuration. Mixing sources is right for absolutes and wrong for ratios.

## One thing this run did not prove

The placement report came back **empty** (`placements: []`): an explicit
`device_map` dict does not populate `hf_device_map` the way `"auto"` does, so
unlike the T4 ×2 run there is no per-module placement record here. Single-card
execution is therefore **inferred from throughput**, not proven from the
device map — the numbers agree to within 1.5% with the batched bench's
independently written `device_map="cuda:0"` cells, and sit 25% above the
sharded run. That is strong corroboration from two directions, but it is
corroboration, not the direct evidence the `large` claim rests on.


---

# What the second T4 is actually worth (A3)

Measured 2026-09-07, kernel `polyforge-tier-bench-2gpu`, script at
`kernels/polyforge-tier-bench-2gpu/`, raw output `tier_bench_2gpu.csv`.
`PROMPTS`, `pad_batch()` and `chat_texts()` are lifted verbatim from
`kaggle_tier_bench_batched.py` (checked mechanically, not by eye), so padding
and prompt shape cannot drift between the two benches. One replica per card,
started through a `threading.Barrier` so neither card starts late. Both the
1-GPU and 2-GPU arms run **in the same session on the same hardware**, so the
scaling column does not depend on cross-run comparability.

| tier | batch | 1 GPU rps | 2 GPU rps | scaling |
|---|---|---|---|---|
| small | 1 | 0.60 | 0.78 | 1.294× |
| small | 8 | 5.13 | 5.92 | 1.153× |
| small | 32 | 19.75 | 23.51 | 1.190× |
| small | 64 | 36.51 | 46.49 | 1.273× |
| mid | 1 | 0.41 | 0.52 | 1.292× |
| mid | 8 | 3.30 | 4.08 | 1.237× |
| mid | 32 | 9.74 | 14.69 | 1.508× |
| mid | 64 | 13.74 | 26.54 | **1.931×** |

**A second card is not a second card's worth of throughput** — except in the
one cell where the GPU is genuinely the bottleneck.

## The pattern, and what explains it

Sorting by how GPU-bound each cell is makes it monotone: scaling rises with
the 1-GPU wall time. Two facts locate the ceiling.

**`small` is not compute-bound at all.** Its 1-GPU wall moves **+5.0%** while
the batch grows **64×** (1.669 s → 1.753 s). Sixty-four times the tokens for
five percent more time means the GPU is very nearly idle and the elapsed time
is almost entirely host-side. `mid`, by contrast, grows **+88.9%** over the
same sweep.

**The second-card penalty is a near-constant, not a proportional cost.** Wall
time added by running both cards:

| tier | batch=1 | batch=8 | batch=32 | batch=64 |
|---|---|---|---|---|
| small | 0.91 s | 1.15 s | 1.10 s | 1.00 s |
| mid | 1.35 s | 1.49 s | 1.07 s | **0.17 s** |

For `small` it sits near 1 s regardless of batch. GPU-side contention would
scale with the work; a fixed cost per call does not. And it collapses to
0.17 s exactly where GPU work per call becomes large.

**Most likely mechanism (inference, not profiled):** `model.generate()` runs a
Python decode loop — 48 iterations, each doing host-side logits processing,
stopping-criteria checks and cache bookkeeping. Two threads contend for the
GIL over that loop. Where GPU compute per step is small, the host loop is the
critical path and the threads serialise; where it is large (`mid` at batch
64), GPU execution overlaps and hides the contention. The constant-penalty
shape and its collapse at high GPU load both fit this; neither fits GPU
contention.

## What this does and does not license

- The batched bench's rows **are** understated, but by a **cell-dependent
  1.15×–1.93×**, not a flat 2×. Do not multiply that table by two.
- The ceiling measured here is **host-side and engine-specific**, so it is a
  property of the transformers decode loop rather than of the two T4s. This
  file already discloses transformers-instead-of-vLLM as a deliberate
  deviation; this is that deviation showing up in a second place. What a
  serving stack with a native decode loop would achieve on this hardware is
  **not measured here and is not claimed**.
- `mid` at batch 64 reaching 1.931× shows the *hardware* scales close to
  linearly once it is the bottleneck. That is the honest ceiling statement:
  the cards are fine, the harness is the limit.


---

# What the measured tier ratio is a ratio OF

The corrected ratio above, **1 : 1.475 : 1.518**, is the right number for the
question `PREREG_TIER_RATIO_V2.md` asks — *what does a request cost to serve
on this substrate* — and it is what that re-run used. It is **not** the
models' intrinsic capacity ratio, and using it as one is a mistake this file
should stop before it happens.

## The measurement

Decode is memory-bandwidth-bound: each token reads the whole weight set once.
If measured TPOT is `weights/bandwidth + fixed overhead`, the TPOT *gap*
between two tiers should equal their weight-read gap, and the overhead should
fall out as a constant. Against `tier_bench_1gpu.csv` on one T4 (320 GB/s):

| tier | fp16 weights | weights/bandwidth | measured TPOT | residual |
|---|---|---|---|---|
| small | 0.99 GB | 3.09 ms | 32.22 ms | **29.13 ms** |
| mid | 6.17 GB | 19.28 ms | 48.00 ms | **28.72 ms** |

Predicted gap 16.19 ms, measured gap 15.78 ms — **97.5% agreement** — and the
residual is a **constant ~29 ms per token, independent of model size**. That
is the Python decode loop, and it is the same ceiling the two-GPU scaling
measurement found from the opposite direction (a near-constant ~1 s
second-card penalty that collapses to 0.17 s once GPU work dominates). Two
independent measurements, one mechanism.

## Why it matters

A fixed per-token cost added to every tier alike **compresses all ratios
toward 1**. The bandwidth-bound ratios are 1 : 6.26 : 15.43; measured under
transformers they are 1 : 1.475 : 1.518.

So there are three candidate numbers for `large`, and they answer different
questions:

| value | what it is | good for |
|---|---|---|
| 16.640x | P100, CPU-offloaded | nothing directly — but see below |
| **1.518x** | T4 x2, transformers | **serving cost on this substrate** (what the price re-run needed) |
| 15.43x | weights read per token | **capacity consumption** (what a work-unit reading needs) |

**And an accident worth recording, because it reverses the obvious move.** The
offload-dominated 16.64x sits **within 8%** of the capacity-bound 15.43x,
because offload slows a model for a reason that scales with its weight size.
The transformers-measured 1.518x is off by **13.91**. So for the *capacity*
reading in `PREREG_TIER_WU.md`, "correcting" 16.64 to 1.518 would replace a
nearly-right number with a badly wrong one. That re-run is **not** being done;
see that file's superseding note.

## What would settle it

A serving engine whose fixed overhead does not dominate. The B1 substrate is
exactly that (`PREREG_WAVE4_LIVE_PLANE.md` session-44 amendment: vLLM on a
rented host), so re-running this bench there yields ratios that approach the
capacity reading and can be quoted for both questions. **Until then, quote
1 : 1.475 : 1.518 only as serving cost on this substrate, never as how much
capacity a tier consumes.**
