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
  The small/mid rows ran fully on-GPU.
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
