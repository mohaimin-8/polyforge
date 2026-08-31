"""L3: per-tier throughput and latency UNDER MICRO-BATCHING, on a free Kaggle GPU.

`TIER_BENCH.md` measured single-stream: one request at a time, a fixed 48-token
completion, n=25 sequential. That is the substrate `kaggle_tier_server.py` used
to serve, and it delivered ~0.5 req/s -- against a `wave4_live_plane` cell that
needs ~64 AI rps at base and ~110 at burst peak. The 2026-08-31 matrix died
there, 48.3% of requests failed.

`docs/ZERO_COST_ROADMAP.md` argues that 0.5 req/s is a batch-size-1 SOFTWARE
limit rather than the card's: decode is memory-bandwidth-bound, so a batched
step reads the weights ONCE for the whole batch. This kernel is where that
argument becomes a measurement, or fails to.

Runs as a Kaggle *script kernel* (`kaggle kernels push`, GPU + internet on) and
writes `tier_bench_batched.csv` as the kernel's output.

WHAT IT MEASURES, per tier (small = Qwen2.5-0.5B, mid = Qwen2.5-3B):

  * batch sizes 1, 8, 32, 64 -- the throughput curve. If wall time per batch is
    roughly flat in batch size, decode is bandwidth-bound as claimed and
    throughput scales with the batch.
  * output lengths 48 and 96 -- TWO lengths, which is what separates prefill
    from decode. `TIER_BENCH.md` used one, so TTFT/TPOT was underivable from it
    (this is M1, re-scoped from the sim to here):
        TPOT = (t_96 - t_48) / 48
        TTFT = t_48 - 48 * TPOT
  * a batched-vs-serial EXACTNESS check on the real models. Greedy decode at a
    fixed length is deterministic, so batched output must be token-identical to
    serial. `eval/tests/test_tier_server.py` pins this against a 2 MB
    random-weight Llama; this is the same assertion against the models that
    actually serve. If it fails, every batched throughput number below is
    measuring a substrate that returns wrong answers.

WHAT IT DOES NOT DECIDE. Amendment clause 3 (`PREREG_WAVE4_LIVE_PLANE.md`) is a
gate on the SERVED path under the cell's concurrency, and belongs to
`tunnel_preflight.py`'s concurrent stage, not here. This kernel measures the
card. A tier that OOMs records an error row rather than aborting -- a partial
table is still a measurement (TIER_BENCH.md's own convention).
"""

from __future__ import annotations

import csv
import gc
import statistics
import sys
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

TIERS = {
    "small": "Qwen/Qwen2.5-0.5B-Instruct",
    "mid": "Qwen/Qwen2.5-3B-Instruct",
}
BATCH_SIZES = (1, 8, 32, 64)
OUTPUT_LENGTHS = (48, 96)
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


def generate(model, tok, texts, new_tokens):
    inputs = pad_batch(tok, texts).to(model.device)
    with torch.no_grad():
        out = model.generate(**inputs,
                             max_new_tokens=new_tokens,
                             min_new_tokens=new_tokens,
                             do_sample=False,
                             pad_token_id=tok.pad_token_id or tok.eos_token_id)
    width = int(inputs["input_ids"].shape[1])
    return [out[i][width:].tolist() for i in range(len(texts))]


def exactness(model, tok) -> tuple[bool, str]:
    """Batched decode must be token-identical to serial decode."""
    try:
        templated = [
            tok.apply_chat_template([{"role": "user", "content": p}],
                                    tokenize=False, add_generation_prompt=True)
            for p in PROMPTS
        ]
        serial = [generate(model, tok, [text], 8)[0] for text in templated]
        batched = generate(model, tok, templated, 8)
        return (batched == serial), ("identical" if batched == serial
                                     else "DIVERGED from serial")
    except Exception as err:  # noqa: BLE001
        return False, f"{type(err).__name__}: {err}"


def main() -> int:
    rows = []
    print(f"torch {torch.__version__} cuda={torch.cuda.is_available()} "
          f"devices={torch.cuda.device_count()}", flush=True)
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            print(f"  gpu{i}: {torch.cuda.get_device_name(i)}", flush=True)

    for tier, repo in TIERS.items():
        print(f"\n=== {tier} <- {repo} ===", flush=True)
        try:
            tok = AutoTokenizer.from_pretrained(repo)
            model = AutoModelForCausalLM.from_pretrained(
                repo, torch_dtype=torch.float16, device_map="cuda:0")
            model.eval()
        except Exception as err:  # noqa: BLE001
            rows.append({"tier": tier, "batch": "", "new_tokens": "",
                         "error": f"{type(err).__name__}: {err}"})
            print(f"  LOAD FAILED: {err}", flush=True)
            continue

        ok, detail = exactness(model, tok)
        print(f"  batched-vs-serial exactness: {detail}", flush=True)
        rows.append({"tier": tier, "batch": "exactness", "new_tokens": 8,
                     "exact": ok, "error": "" if ok else detail})

        for new_tokens in OUTPUT_LENGTHS:
            for batch in BATCH_SIZES:
                texts = chat_texts(tok, batch)
                try:
                    for _ in range(WARMUPS):
                        generate(model, tok, texts, new_tokens)
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()
                    samples = []
                    for _ in range(REPEATS):
                        t0 = time.perf_counter()
                        generate(model, tok, texts, new_tokens)
                        if torch.cuda.is_available():
                            torch.cuda.synchronize()
                        samples.append(time.perf_counter() - t0)
                    wall = statistics.median(samples)
                    rows.append({
                        "tier": tier, "batch": batch, "new_tokens": new_tokens,
                        "wall_s": round(wall, 4),
                        "per_request_ms": round(wall * 1000.0, 1),
                        "throughput_rps": round(batch / wall, 2),
                        "tokens_per_s": round(batch * new_tokens / wall, 1),
                        "error": "",
                    })
                    print(f"  batch={batch:<3} tokens={new_tokens:<3} "
                          f"{wall * 1000:8.1f} ms  "
                          f"{batch / wall:7.2f} req/s", flush=True)
                except torch.cuda.OutOfMemoryError as err:
                    rows.append({"tier": tier, "batch": batch,
                                 "new_tokens": new_tokens, "error": f"OOM: {err}"})
                    print(f"  batch={batch} tokens={new_tokens}: OOM", flush=True)
                    torch.cuda.empty_cache()
                except Exception as err:  # noqa: BLE001
                    rows.append({"tier": tier, "batch": batch,
                                 "new_tokens": new_tokens,
                                 "error": f"{type(err).__name__}: {err}"})
                    print(f"  batch={batch} tokens={new_tokens}: {err}", flush=True)

        del model, tok
        gc.collect()
        torch.cuda.empty_cache()

    fields = ["tier", "batch", "new_tokens", "wall_s", "per_request_ms",
              "throughput_rps", "tokens_per_s", "exact", "error"]
    with open("tier_bench_batched.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    print("\nwrote tier_bench_batched.csv", flush=True)

    # TTFT/TPOT from the two output lengths, at batch 1 (the shape the
    # committed single-stream table is comparable to).
    print("\n=== prefill/decode split (batch=1) ===", flush=True)
    for tier in TIERS:
        pick = {r["new_tokens"]: r for r in rows
                if r.get("tier") == tier and r.get("batch") == 1 and not r.get("error")}
        if 48 in pick and 96 in pick:
            t48, t96 = pick[48]["wall_s"], pick[96]["wall_s"]
            tpot = (t96 - t48) / 48.0
            ttft = t48 - 48.0 * tpot
            print(f"  {tier:5s} TPOT {tpot * 1000:6.2f} ms/token   "
                  f"TTFT {ttft * 1000:7.1f} ms", flush=True)
        else:
            print(f"  {tier:5s} not derivable (a length is missing)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
