"""Does vLLM rescue the FREE route for B1? (session 44)

Session 44 measured the free route infeasible for the frozen cell -- but every
one of those numbers was taken under `transformers`, and the same session then
showed that ~28.7 ms of `mid`'s 48.00 ms TPOT is FIXED HOST OVERHEAD (the
Python decode loop), not GPU work: predicted-vs-measured tier gap agreed to
97.5%, and the residual was constant across model sizes.

vLLM removes most of that overhead. So the "infeasible" verdict may be a
property of the engine rather than the hardware, and this kernel measures it
instead of arguing about it.

The two gates it has to clear, both frozen elsewhere:

  clause 3   slowest tier p95 < 2500 ms for 48 new tokens
             (PREREG_WAVE4_LIVE_PLANE.md amendment; on the split-host route
             the tunnel's 50-300 ms comes out of whatever headroom is left)
  throughput ~64 AI rps at base, ~110 at burst peak
             (kaggle_tier_server.py: 8 tenants x 8 AI rps)

Protocol matches the rest of the tier bench: 48 new tokens, min == max so
every request decodes exactly 48 greedy steps, WARMUP then RUNS sequential
for latency, then one large batch for throughput.

T4 is sm_75 and has NO bfloat16, while Qwen2.5 ships bf16 weights -- dtype is
forced to float16 or the load fails on this pool.
"""

import gc
import importlib.util
import os
import statistics
import subprocess
import sys
import time

# Install vLLM and RE-EXEC before torch is imported.
#
# Attempt 3 removed torch and installed vllm==0.11.0 in-process and still died:
#   RuntimeError: function '_has_torch_function' already has a docstring
# That is not a compatibility failure. This module had already imported torch,
# so the old C extension was resident and the freshly installed one collided
# registering the same symbols. Uninstalling a loaded torch cannot unload it.
#
# kaggle_tier_bench.py already carries the fix for exactly this: install, then
# os.execv the interpreter so the next process starts clean, guarded by an env
# var so it happens once. Same pattern here, hoisted above `import torch`
# because that import is what poisons the process.
if not os.environ.get("VLLM_PROBE_REEXEC"):
    if importlib.util.find_spec("vllm") is None:
        print("installing vllm==0.11.0 and its contemporaries ...", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q",
                        "torch", "torchvision", "torchaudio"], check=False)
        # transformers must be pinned WITH vllm, not left to the image.
        # v4 got vllm 0.11.0 importing and reaching model load, then died on
        #   AttributeError: Qwen2Tokenizer has no attribute
        #   all_special_tokens_extended
        # vllm 0.11.0 declares `transformers>=4.55.2` -- a FLOOR, not a pin --
        # so pip kept the image's much newer transformers, which dropped that
        # attribute. Pin a contemporary of vllm 0.11.0 so the tokenizer API
        # matches what it was built against.
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                            "vllm==0.11.0", "transformers==4.56.2"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            for line in (r.stderr or r.stdout).strip().splitlines()[-4:]:
                print("  pip: " + line[:200], flush=True)
        print("re-executing on the new torch ...", flush=True)
    os.environ["VLLM_PROBE_REEXEC"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)

import torch

N_PREDICT = 48
WARMUP = 3
RUNS = 25
THROUGHPUT_PROMPTS = 256
PROMPT = (
    "Summarize the trade-offs between reactive and proactive autoscaling "
    "for multi-tenant model serving, mentioning cost, latency, and fairness "
    "in your answer. Keep the answer factual and complete."
)
TIERS = [
    ("small", "Qwen/Qwen2.5-0.5B-Instruct", 1),
    ("mid", "Qwen/Qwen2.5-3B-Instruct", 1),
    ("large", "Qwen/Qwen2.5-7B-Instruct", 2),   # 15 GB fp16 -> needs both T4s
]


def bench(name, model_id, tp_size, LLM, SamplingParams):
    print(f"\n=== {name} <- {model_id}  (tensor_parallel={tp_size}) ===", flush=True)
    llm = LLM(model=model_id, dtype="float16", tensor_parallel_size=tp_size,
              gpu_memory_utilization=0.85, max_model_len=2048,
              enforce_eager=False, disable_log_stats=True)
    sp = SamplingParams(max_tokens=N_PREDICT, min_tokens=N_PREDICT,
                        temperature=0.0)

    for _ in range(WARMUP):
        llm.generate([PROMPT], sp, use_tqdm=False)

    lat = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        llm.generate([PROMPT], sp, use_tqdm=False)
        lat.append(time.perf_counter() - t0)
    lat.sort()
    mean = statistics.fmean(lat)
    p95 = lat[max(0, round(0.95 * RUNS) - 1)]
    tpot = mean / N_PREDICT

    t0 = time.perf_counter()
    llm.generate([PROMPT] * THROUGHPUT_PROMPTS, sp, use_tqdm=False)
    wall = time.perf_counter() - t0
    rps = THROUGHPUT_PROMPTS / wall

    print(f"  latency  mean {mean*1000:8.1f} ms   p95 {p95*1000:8.1f} ms"
          f"   TPOT {tpot*1000:6.2f} ms/token", flush=True)
    print(f"  clause 3 (<2500 ms p95): "
          f"{'PASS' if p95*1000 < 2500 else 'FAIL'}"
          f"   headroom {2500 - p95*1000:8.1f} ms", flush=True)
    print(f"  throughput {THROUGHPUT_PROMPTS} reqs in {wall:.2f}s = "
          f"{rps:7.2f} rps   (base needs 64, peak 110)", flush=True)

    del llm
    gc.collect()
    torch.cuda.empty_cache()
    return {"tier": name, "mean_ms": round(mean * 1000, 1),
            "p95_ms": round(p95 * 1000, 1), "tpot_ms": round(tpot * 1000, 2),
            "rps": round(rps, 2)}


def main():
    print("cuda devices:", torch.cuda.device_count(),
          [torch.cuda.get_device_name(i)
           for i in range(torch.cuda.device_count())], flush=True)
    # Attempt 1 failed with `ImportError: libcudart.so.13` -- an unpinned
    # `pip install vllm` takes the latest wheel, which is compiled against
    # CUDA 13, while this pool ships CUDA 12.8 (torch 2.10.0+cu128). Nothing
    # to do with sm_75. The fix is a release built against cu12x, which brings
    # its own matching torch; try a ladder in one run rather than burning a
    # kernel per guess, and report which one imports.
    try:
        from vllm import LLM, SamplingParams
        import vllm
        print("vllm", vllm.__version__, "| torch", torch.__version__, flush=True)
    except Exception as err:
        print(f"vllm import STILL fails after re-exec: "
              f"{type(err).__name__}: {err}"[:300], flush=True)
        print("", flush=True)
        print("VLLM DOES NOT RUN ON THIS IMAGE. The free pool serves tiers with",
              flush=True)
        print("transformers only, so session 44's measurements stand and B1's",
              flush=True)
        print("frozen cell needs a provisioned host.", flush=True)
        return

    rows = []
    for name, model_id, tp in TIERS:
        try:
            rows.append(bench(name, model_id, tp, LLM, SamplingParams))
        except Exception as err:            # a partial table beats no table
            print(f"  {name} FAILED: {type(err).__name__}: {err}"[:400], flush=True)
            rows.append({"tier": name, "mean_ms": "", "p95_ms": "",
                         "tpot_ms": "", "rps": "",
                         "error": f"{type(err).__name__}: {err}"[:300]})
            gc.collect(); torch.cuda.empty_cache()

    import csv
    with open("vllm_probe.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tier", "mean_ms", "p95_ms",
                                          "tpot_ms", "rps", "error"])
        w.writeheader()
        for r in rows:
            r.setdefault("error", "")
            w.writerow(r)
    print("\nwrote vllm_probe.csv", flush=True)


if __name__ == "__main__":
    main()
