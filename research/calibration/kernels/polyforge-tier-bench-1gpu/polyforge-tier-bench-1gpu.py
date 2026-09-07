"""Phase 6 (GPU path), single-card: small/mid absolutes + direct TTFT.

Two corrections to earlier runs, same protocol otherwise (PROMPT, N_PREDICT=48,
WARMUP=3, RUNS=25, greedy, fp16, same p95 formula):

  A1. `device_map={"": 0}` instead of "auto". `small` and `mid` both fit on one
      T4; "auto" split them across both cards and charged ~26% in cross-GPU
      transfer for nothing. `large` is absent because it genuinely needs the
      pair. These are the best-achievable single-stream absolutes.
  A2. TTFT is measured by timing a 1-token generation rather than derived as
      t48 - 48*TPOT. The derived form produced -7.4 ms for mid -- prefill sat
      below the noise floor of that subtraction. A direct measurement cannot
      go negative.

Runs as a Kaggle *script kernel* (pushed via `kaggle kernels push`, GPU +
internet enabled) and writes `tier_bench_1gpu.csv` as the kernel's output.

What it measures, per tier stand-in:
    small -> Qwen/Qwen2.5-0.5B-Instruct
    mid   -> Qwen/Qwen2.5-3B-Instruct
    large -> Qwen/Qwen2.5-7B-Instruct  (fp16, sharded across both T4s)
- time-to-last-token for a fixed 48-token completion (same request shape as
  the CPU-path congestion calibration), sequential n=25 after 3 warmups
- single-stream decode tokens/sec
The sim's tier constants this checks: TIER_BASE_LATENCY_MS's ordering/ratios
and TIER_COST_USD_PER_REQ's 1:10:100 shape (via measured GPU-seconds per
request x a rented-GPU $/s — that arithmetic lands in CALIBRATION.md).

Deliberate deviation from V2_README's Phase 6 wording ("vLLM serving"):
the engine is Hugging Face transformers, not vLLM — Kaggle images ship
torch+transformers preinstalled, while vLLM needs a pip install and has
GPU-architecture constraints the free pool does not guarantee. The
measurand (single-stream per-tier latency/throughput shape) is unchanged;
absolute serving-optimized numbers would be lower for every tier alike.

A tier that fails (e.g. OOM if the pool hands out a single small GPU)
records an error row instead of aborting the run — a partial table is
still a measurement.
"""

from __future__ import annotations

import csv
import gc
import os
import statistics
import subprocess
import sys
import time

import torch

# The free pool may grant a P100 (compute 6.0): current torch wheels ship no
# sm_60 kernels ("no kernel image is available" — run 2 of this kernel).
# Fall back once to the cu118 build that still carries them, then re-exec.
if torch.cuda.is_available() and torch.cuda.get_device_capability(0)[0] < 7 \
        and not os.environ.get("TIER_BENCH_REEXEC"):
    print("pre-sm_70 GPU granted:", torch.cuda.get_device_name(0),
          "- installing cu118 torch and re-executing")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "torch==2.4.1", "--index-url",
                    "https://download.pytorch.org/whl/cu118"], check=True)
    # The image's transformers/accelerate are built against current torch;
    # pin the contemporaries of 2.4.1 or Qwen2's module import fails.
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "transformers==4.46.3", "accelerate==1.1.1"], check=True)
    # The image's torchvision/torchaudio are ABI-bound to the *newer* torch
    # ("operator torchvision::nms does not exist"). transformers treats an
    # absent torchvision as fine and a broken one as fatal — remove them.
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-q", "-y",
                    "torchvision", "torchaudio"], check=False)
    os.environ["TIER_BENCH_REEXEC"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)

TIERS = {
    "small": "Qwen/Qwen2.5-0.5B-Instruct",
    "mid": "Qwen/Qwen2.5-3B-Instruct",
}
N_PREDICT = 48
WARMUP = 3
RUNS = 25
PROMPT = (
    "Summarize the trade-offs between reactive and proactive autoscaling "
    "for multi-tenant model serving, mentioning cost, latency, and fairness "
    "in your answer. Keep the answer factual and complete."
)


def bench_tier(name: str, model_id: str) -> dict:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=torch.float16, device_map={"": 0}
    )
    model.eval()
    device_map = getattr(model, "hf_device_map", None) or {}
    print(f"  placements: {sorted({str(v) for v in device_map.values()})}",
          flush=True)
    inputs = tokenizer(PROMPT, return_tensors="pt").to(model.device)

    def generate(n_tokens: int = N_PREDICT) -> None:
        with torch.no_grad():
            model.generate(
                **inputs,
                max_new_tokens=n_tokens,
                min_new_tokens=n_tokens,  # fixed length: homogeneous requests
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        torch.cuda.synchronize()

    def timed(n_tokens: int) -> list:
        for _ in range(WARMUP):
            generate(n_tokens)
        out = []
        for _ in range(RUNS):
            t0 = time.perf_counter()
            generate(n_tokens)
            out.append(time.perf_counter() - t0)
        out.sort()
        return out

    latencies = timed(N_PREDICT)
    mean = statistics.fmean(latencies)

    # TTFT measured, not extrapolated: one token = prefill + a single decode
    # step. TPOT is then the marginal cost of the remaining N_PREDICT-1
    # tokens, so neither figure can be driven negative by noise.
    first = timed(1)
    ttft = statistics.fmean(first)
    tpot = (mean - ttft) / (N_PREDICT - 1)
    print(f"  TTFT {ttft * 1000:.1f} ms (direct, n={RUNS})   "
          f"TPOT {tpot * 1000:.2f} ms/token", flush=True)
    row = {
        "tier": name, "model": model_id, "n": RUNS, "error": "",
        "mean_ms": round(mean * 1000, 1),
        "p95_ms": round(latencies[max(0, round(0.95 * RUNS) - 1)] * 1000, 1),
        "tokens_per_s": round(N_PREDICT / mean, 2),
        "ttft_ms": round(ttft * 1000, 1),
        "tpot_ms": round(tpot * 1000, 2),
    }
    del model
    gc.collect()
    torch.cuda.empty_cache()
    return row


def main() -> None:
    print("cuda devices:", torch.cuda.device_count(),
          [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])
    rows = []
    for name, model_id in TIERS.items():
        print(f"benchmarking {name} = {model_id} ...")
        try:
            rows.append(bench_tier(name, model_id))
        except Exception as exc:  # partial table beats no table
            gc.collect()
            torch.cuda.empty_cache()
            rows.append({"tier": name, "model": model_id, "n": 0,
                         "error": f"{type(exc).__name__}: {exc}"[:300],
                         "mean_ms": "", "p95_ms": "", "tokens_per_s": "",
                         "ttft_ms": "", "tpot_ms": ""})
        print(rows[-1])
    with open("tier_bench_1gpu.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print("wrote tier_bench_1gpu.csv")


if __name__ == "__main__":
    main()
