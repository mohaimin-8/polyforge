"""Does the second T4 actually double throughput? (roadmap A3)

`kaggle_tier_bench_batched.py` loaded with `device_map="cuda:0"`, so gpu1 sat
idle for its entire 468 s run and every throughput row it published is a
one-card number taken from a two-card allocation. This measures what the
second card is worth: one independent replica per GPU, both fed the same
batch concurrently.

PROMPTS, pad_batch() and chat_texts() are lifted verbatim from that script, so
padding and prompt shape cannot drift between the two benches.

Deliberate protocol deviation, and the reason for it: inputs are tokenised
ONCE per (tier, batch, device) and the timed region covers only
model.generate() plus a per-device synchronize. The batched bench tokenises
inside its timed region, which is fine for a single stream but would serialise
on the GIL here and understate concurrency. Both arms of this run -- 1 GPU and
2 GPU -- use the identical pre-tokenised protocol, so the scaling ratio is
internally consistent; only the absolutes differ from the batched bench.
"""

from __future__ import annotations

import csv
import gc
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

TIERS = {
    "small": "Qwen/Qwen2.5-0.5B-Instruct",
    "mid": "Qwen/Qwen2.5-3B-Instruct",
}
BATCH_SIZES = (1, 8, 32, 64)
NEW_TOKENS = 48        # output length is orthogonal to the scaling question
WARMUPS = 2
REPEATS = 5

# Ragged on purpose: equal-length prompts pad to nothing, so a padding bug
# would not show up in the exactness check.
PROMPTS = [
    "Summarise the causes of the 1929 crash.",
    "Write a haiku about distributed systems and the way they fail at 3am.",
    "2 + 2 =",
    "Explain memory bandwidth to a systems engineer in three sentences.",
]


def pad_batch(tok, texts):
    """LEFT padding. Decoder-only generation continues from the last position,
    so right padding continues from PAD and returns fluent nonsense."""
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    previous = tok.padding_side
    tok.padding_side = "left"
    try:
        return tok(texts, return_tensors="pt", padding=True)
    finally:
        tok.padding_side = previous


def chat_texts(tok, n):
    out = []
    for i in range(n):
        prompt = PROMPTS[i % len(PROMPTS)]
        out.append(tok.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False, add_generation_prompt=True))
    return out


def timed(models, batches, new_tokens, pad_id):
    """One batch per model, all models firing together. Returns wall seconds.

    A barrier makes the threads start in the same instant; without it a
    late-starting second card would look like poor scaling.
    """
    n = len(models)
    barrier = threading.Barrier(n)

    def work(i):
        barrier.wait()
        with torch.no_grad():
            models[i].generate(**batches[i],
                               max_new_tokens=new_tokens,
                               min_new_tokens=new_tokens,
                               do_sample=False,
                               pad_token_id=pad_id)
        torch.cuda.synchronize(models[i].device)

    out = []
    with ThreadPoolExecutor(max_workers=n) as ex:
        for _ in range(WARMUPS):
            list(ex.map(work, range(n)))
        for _ in range(REPEATS):
            t0 = time.perf_counter()
            list(ex.map(work, range(n)))
            out.append(time.perf_counter() - t0)
    return out


def main() -> None:
    n_gpu = torch.cuda.device_count()
    print("cuda devices:", n_gpu,
          [torch.cuda.get_device_name(i) for i in range(n_gpu)], flush=True)
    if n_gpu < 2:
        print("FATAL: this measurement needs 2 GPUs; got", n_gpu, flush=True)
        raise SystemExit(1)

    rows = []
    for tier, repo in TIERS.items():
        print("", flush=True)
        print(f"=== {tier} <- {repo} ===", flush=True)
        tok = AutoTokenizer.from_pretrained(repo)
        pad_id = tok.pad_token_id or tok.eos_token_id
        replicas = []
        for dev in range(n_gpu):
            m = AutoModelForCausalLM.from_pretrained(
                repo, dtype=torch.float16, device_map={"": dev})
            m.eval()
            replicas.append(m)
        print(f"  replicas on: {[str(m.device) for m in replicas]}", flush=True)

        for batch in BATCH_SIZES:
            texts = chat_texts(tok, batch)
            per_dev = [pad_batch(tok, texts).to(replicas[d].device)
                       for d in range(n_gpu)]

            one = statistics.fmean(timed(replicas[:1], per_dev[:1],
                                         NEW_TOKENS, pad_id))
            two = statistics.fmean(timed(replicas, per_dev,
                                         NEW_TOKENS, pad_id))
            rps1 = batch / one
            rps2 = (batch * n_gpu) / two
            print(f"  batch={batch:<3d} 1gpu {one:7.4f}s {rps1:8.2f} rps   "
                  f"2gpu {two:7.4f}s {rps2:8.2f} rps   "
                  f"scaling {rps2 / rps1:.3f}x", flush=True)
            rows.append({"tier": tier, "batch": batch,
                         "new_tokens": NEW_TOKENS,
                         "wall_1gpu_s": round(one, 4),
                         "wall_2gpu_s": round(two, 4),
                         "rps_1gpu": round(rps1, 2),
                         "rps_2gpu": round(rps2, 2),
                         "scaling": round(rps2 / rps1, 3)})

        for m in replicas:
            del m
        del replicas
        gc.collect()
        torch.cuda.empty_cache()

    with open("tier_bench_2gpu.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print("", flush=True)
    print("wrote tier_bench_2gpu.csv", flush=True)


if __name__ == "__main__":
    main()
