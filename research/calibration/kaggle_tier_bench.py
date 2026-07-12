"""Phase 6 (GPU path): per-tier latency/throughput table on a free T4.

USER-RUN: paste this file into a Kaggle notebook (Settings -> Accelerator ->
GPU T4 x2, or Colab free T4) and run it. It needs no secrets and no gated
models. Bring back `tier_bench.csv`; `CALIBRATION.md` then gains the
empirical tier table replacing the assumed TIER_BASE_LATENCY_MS /
TIER_COST_USD_PER_REQ *shape* checks in research/jcac_sim/model.py.

What it measures, per tier stand-in:
    small -> Qwen/Qwen2.5-0.5B-Instruct
    mid   -> Qwen/Qwen2.5-3B-Instruct
    large -> Qwen/Qwen2.5-7B-Instruct (AWQ 4-bit; fp16 does not fit a T4)
- time-to-last-token for a fixed 48-token completion (matches the CPU-path
  request shape), sequential n=25 after 3 warmups -> mean/p95 latency
- single-stream decode tokens/sec
The tier *price* ratio the sim assumes (1 : 10 : 100 per request) can then be
sanity-checked against measured GPU-seconds per request times a rented-GPU
$/s — that arithmetic lives in CALIBRATION.md, not here.

Setup cell (run first, ~3 min):
    !pip install -q vllm

Then run this file. Runtime ~20-30 min total (model downloads dominate).
"""

from __future__ import annotations

import csv
import statistics
import time

TIERS = {
    "small": ("Qwen/Qwen2.5-0.5B-Instruct", {}),
    "mid": ("Qwen/Qwen2.5-3B-Instruct", {}),
    "large": ("Qwen/Qwen2.5-7B-Instruct-AWQ", {"quantization": "awq"}),
}
N_PREDICT = 48
WARMUP = 3
RUNS = 25
PROMPT = (
    "Summarize the trade-offs between reactive and proactive autoscaling "
    "for multi-tenant model serving, mentioning cost, latency, and fairness "
    "in your answer. Keep the answer factual and complete."
)


def bench_tier(name: str, model: str, extra: dict) -> dict:
    from vllm import LLM, SamplingParams

    llm = LLM(model=model, max_model_len=2048, gpu_memory_utilization=0.85, **extra)
    params = SamplingParams(temperature=0.0, max_tokens=N_PREDICT, ignore_eos=True)
    for i in range(WARMUP):
        llm.generate([f"[warm {i}] {PROMPT}"], params, use_tqdm=False)
    latencies = []
    for i in range(RUNS):
        t0 = time.perf_counter()
        llm.generate([f"[req {i}] {PROMPT}"], params, use_tqdm=False)
        latencies.append(time.perf_counter() - t0)
    latencies.sort()
    mean = statistics.fmean(latencies)
    row = {
        "tier": name, "model": model, "n": RUNS,
        "mean_ms": round(mean * 1000, 1),
        "p95_ms": round(latencies[max(0, round(0.95 * RUNS) - 1)] * 1000, 1),
        "tokens_per_s": round(N_PREDICT / mean, 2),
    }
    del llm  # free VRAM before the next tier
    import gc, torch
    gc.collect()
    torch.cuda.empty_cache()
    return row


def main() -> None:
    rows = []
    for name, (model, extra) in TIERS.items():
        print(f"benchmarking {name} = {model} ...")
        rows.append(bench_tier(name, model, extra))
        print(rows[-1])
    with open("tier_bench.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print("wrote tier_bench.csv — download it and hand it back to the repo")


if __name__ == "__main__":
    main()
