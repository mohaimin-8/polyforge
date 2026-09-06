"""Semantic-cache HIT PRECISION on real LMSYS-Chat-1M — the correctness
denominator the hit-rate protocol (SEMANTIC_CACHE.md) deliberately lacked.

MOTIVATION (field gap this closes): production semantic-cache literature in
2025-26 reports that hit *quality* — the rate at which a "hit" serves a
wrong answer — is the binding constraint, not hit rate. PolyForge's cache
segment so far reports hit-rate economics only. This protocol measures, on
the same real dataset, the probability that a served cache hit would have
been a correct answer.

PROTOCOL — FROZEN BEFORE THE FIRST RUN (2026-07-13, session 17; committed
and pushed before any precision number was seen; the dataset itself was
already on disk from the Phase B hit-rate run, so the discipline here is
metric-freeze, not download-freeze):

1. Data: (prompt, response, model, first_turn) tuples — every user turn
   whose immediately following message is a non-empty assistant turn, i.e.
   the answer the deployment actually returned for that prompt. Reservoir
   sample n = 100,000 tuples, seed 42, streaming uniformly across all six
   parquet files. (100k, not the hit-rate run's 200k: this protocol encodes
   prompts AND responses — double the encode — and 50k queries yield
   >10,000 hits at the 0.85 operating point, ample for a proportion.)
2. Embedder: `sentence-transformers/all-MiniLM-L6-v2` (the protocol's
   standard encoder), for prompts and responses alike. Responses truncated
   to 1,000 characters (MiniLM's 256-token window bounds meaningful input
   anyway; stated, not hidden). No fallback embedder here: precision of a
   lexical fallback is not a semantic claim, so the script refuses to run
   without the real encoder.
3. Split: insert first half, query second half — identical to the
   hit-rate protocol. Cache config under test: adaptive/unbounded (the
   headline config), i.e. nearest neighbor over the whole inserted half.
4. Cache semantics: a query is a HIT at prompt threshold tau if its
   nearest inserted PROMPT has cosine >= tau; the cache serves that
   neighbor's stored RESPONSE (argmax neighbor, standard cache behavior).
5. Correctness proxy: response agreement = cosine between the served
   (cached) response embedding and the response the query actually
   received. A hit is COUNTED CORRECT when agreement >= rho.
   HEADLINE rho = 0.70, declared here in advance; rho = 0.60 and 0.80 are
   reported as sensitivity rows. Precision(tau) = P(agreement >= rho | hit
   at tau). Also reported: incorrect hits per 1,000 queries
   = hit_rate(tau) x (1 - precision(tau)) x 1000.
6. Declared confounders and their directions:
   a. LMSYS responses come from ~25 different models; two correct answers
      from different models diverge stylistically, deflating agreement —
      the measured precision is therefore a conservative LOWER bound.
      The same-model stratum (cached pair's model == query's model) is
      reported at the operating threshold to bound this effect.
   b. Mid-conversation turns depend on context: an identical prompt text
      can require a different answer. Low agreement there is a REAL
      caching error (context-blind serving), so the proxy correctly
      penalizes it. The first-turn-only stratum (both query and cached
      prompt are their conversations' first user turns) is reported as
      the cacheable-traffic view.
7. No single-number "quality-adjusted dollars": pricing an incorrect
   answer is application-specific. The reader gets savings-per-1k and
   incorrect-hits-per-1k side by side, and multiplies by their own harm
   price.
8. One run, seed 42, results to CACHE_PRECISION.md. The stopping rule of
   the campaign applies: this file is not re-run to fish; a re-run
   requires a new declared amendment.

    python cache_hit_precision.py \
        --conversations "../traces/data/lmsys-chat-1m/data/*.parquet"
"""

from __future__ import annotations

import argparse
import glob
import time
from pathlib import Path

import numpy as np

import stats  # noqa: E402  (research/analysis/stats.py)

# Written through stats.record_path so POLYFORGE_ANALYSIS_OUT can redirect it;
# it resolved to this directory unconditionally, so running the script
# overwrote the committed record and the gate could not compare it.
OUT = stats.record_path("CACHE_PRECISION.md")
THRESHOLDS = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
AGREEMENT_HEADLINE = 0.70
AGREEMENT_SENSITIVITY = (0.60, 0.70, 0.80)
OPERATING = 0.85  # the hit-rate protocol's operating threshold
RESPONSE_TRUNC_CHARS = 1000


def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.clip(n, 1e-9, None)


def load_pairs(pattern: str, sample: int, seed: int):
    """Reservoir-sample (prompt, response, model, first_turn) tuples,
    streaming one parquet at a time, uniform over qualifying pairs."""
    import pandas as pd

    paths = sorted(glob.glob(pattern))
    if not paths:
        raise SystemExit(f"no parquet matched {pattern}")
    rng = np.random.default_rng(seed)
    reservoir: list[tuple[str, str, str, bool]] = []
    seen = 0
    for path in paths:
        t0 = time.time()
        df = pd.read_parquet(path, columns=["conversation", "model"])
        for convo, model in zip(df["conversation"], df["model"]):
            user_turn_no = 0
            for i in range(len(convo) - 1):
                msg, nxt = convo[i], convo[i + 1]
                if msg.get("role") != "user" or not msg.get("content"):
                    continue
                user_turn_no += 1
                if nxt.get("role") != "assistant" or not nxt.get("content"):
                    continue
                tup = (msg["content"], nxt["content"][:RESPONSE_TRUNC_CHARS],
                       str(model), user_turn_no == 1)
                seen += 1
                if len(reservoir) < sample:
                    reservoir.append(tup)
                else:
                    j = int(rng.integers(seen))
                    if j < sample:
                        reservoir[j] = tup
        del df
        print(f"  {Path(path).name}: cumulative {seen} pairs "
              f"({time.time() - t0:.0f}s)", flush=True)
    rng.shuffle(reservoir)
    print(f"reservoir: {len(reservoir)} of {seen} qualifying pairs (seed {seed})")
    return reservoir


def encode(texts: list[str], label: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    t0 = time.time()
    vecs = _l2(np.asarray(
        model.encode(texts, batch_size=256, show_progress_bar=False),
        dtype="float64"))
    print(f"encoded {len(texts)} {label} in {time.time() - t0:.0f}s", flush=True)
    return vecs


def nearest_with_argmax(queries: np.ndarray, inserted: np.ndarray,
                        block: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """Exact cosine NN over the whole inserted set, returning both the best
    similarity AND the argmax index (the hit-rate protocol only needed the
    max; serving a response needs to know whose response it is)."""
    best = np.full(len(queries), -1.0)
    who = np.zeros(len(queries), dtype=np.int64)
    for start in range(0, len(inserted), block):
        blk = inserted[start:start + block]
        sims = queries @ blk.T
        idx = sims.argmax(axis=1)
        val = sims[np.arange(len(queries)), idx]
        better = val > best
        best[better] = val[better]
        who[better] = start + idx[better]
    return best, who


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conversations", required=True, help="LMSYS parquet glob")
    ap.add_argument("--sample", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    pairs = load_pairs(args.conversations, args.sample, args.seed)
    prompts = [p for p, _, _, _ in pairs]
    responses = [r for _, r, _, _ in pairs]
    models = np.array([m for _, _, m, _ in pairs])
    first_turn = np.array([f for _, _, _, f in pairs])

    pvecs = encode(prompts, "prompts")
    rvecs = encode(responses, "responses")

    half = len(pairs) // 2
    sims, neighbor = nearest_with_argmax(pvecs[half:], pvecs[:half])

    # Response agreement between the served (cached) response and the
    # response the query actually received.
    agreement = np.einsum("ij,ij->i", rvecs[half:], rvecs[neighbor])
    same_model = models[half:] == models[neighbor]
    both_first = first_turn[half:] & first_turn[neighbor]

    lines = ["# Semantic-cache hit precision on real LMSYS-Chat-1M", ""]
    w = lines.append
    w(f"Protocol frozen in `cache_hit_precision.py` (committed pre-run). "
      f"n = {len(pairs)} (prompt, response) pairs, seed {args.seed}; insert "
      f"first half, query second half; adaptive (unbounded) cache; embedder "
      f"MiniLM-L6-v2 both sides; responses truncated to "
      f"{RESPONSE_TRUNC_CHARS} chars. A hit at prompt-cosine tau serves the "
      f"argmax neighbor's stored response; it counts CORRECT when the "
      f"response-agreement cosine >= rho (headline rho = "
      f"{AGREEMENT_HEADLINE}, declared pre-run).")
    w("")
    w("| tau | hit rate | precision @0.60 | precision @0.70 (headline) | "
      "precision @0.80 | incorrect hits /1k queries (rho=0.70) |")
    w("|---|---|---|---|---|---|")
    op_row = {}
    for tau in THRESHOLDS:
        hits = sims >= tau
        n_hits = int(hits.sum())
        rate = n_hits / len(sims)
        precs = {rho: (float((agreement[hits] >= rho).mean()) if n_hits else float("nan"))
                 for rho in AGREEMENT_SENSITIVITY}
        bad_per_1k = rate * (1.0 - precs[AGREEMENT_HEADLINE]) * 1000
        w(f"| {tau:.2f} | {rate:.3f} | {precs[0.60]:.3f} | {precs[0.70]:.3f} "
          f"| {precs[0.80]:.3f} | {bad_per_1k:.1f} |")
        if tau == OPERATING:
            op_row = {"rate": rate, "prec": precs[AGREEMENT_HEADLINE],
                      "hits": hits, "n_hits": n_hits, "bad_per_1k": bad_per_1k}
    w("")

    hits = op_row["hits"]
    sm = same_model[hits]
    ft = both_first[hits]
    agr = agreement[hits]
    rho = AGREEMENT_HEADLINE

    def stratum(mask, name):
        n = int(mask.sum())
        if n == 0:
            w(f"- {name}: no hits in stratum.")
            return
        p = float((agr[mask] >= rho).mean())
        w(f"- {name}: precision {p:.3f} over {n} hits "
          f"({n / op_row['n_hits']:.0%} of hits).")

    w(f"## Strata at the operating threshold tau = {OPERATING} "
      f"(rho = {rho})")
    w("")
    w(f"Overall: hit rate {op_row['rate']:.1%}, precision "
      f"{op_row['prec']:.3f}, {op_row['bad_per_1k']:.1f} incorrect hits per "
      f"1,000 queries.")
    w("")
    stratum(sm, "Same-model pairs (style confounder removed)")
    stratum(~sm, "Cross-model pairs (style confounder present)")
    stratum(ft, "First-turn-only pairs (context-free, cacheable-traffic view)")
    w("")
    w("Declared readings (directions frozen pre-run, magnitudes measured):")
    w("- Cross-model style divergence deflates agreement, so overall "
      "precision is a conservative LOWER bound; the same-model stratum "
      "bounds the effect.")
    w("- Low agreement on mid-conversation turns is a REAL caching error "
      "(context-blind serving), not a proxy artifact; the first-turn "
      "stratum shows the cache's precision on traffic it should serve.")
    w("- No quality-adjusted dollar figure is reported: pricing a wrong "
      "answer is application-specific. Combine the hit-rate protocol's "
      "savings-per-1k with the incorrect-hits-per-1k column above.")
    w("")
    w("Reproduce: `python cache_hit_precision.py --conversations "
      "'<lmsys parquet glob>'` (gated dataset: HF account + license "
      "accept + token).")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: hit@{OPERATING}={op_row['rate']:.3f} "
          f"precision@rho{rho}={op_row['prec']:.3f}")


if __name__ == "__main__":
    main()
