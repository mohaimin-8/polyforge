"""v2 Phase 3b: semantic-cache hit-rate protocol (LMSYS-Chat-1M-ready).

The standard evaluation (V2_README Phase 3b): embed prompts, insert the
first half, query the second half, sweep the similarity threshold, and
report the hit-rate curve — for a fixed-size cache and for PolyForge's
adaptive (demand-sized) cache — plus the dollar-weighted savings a hit
buys (a hit avoids a tier-priced model call, the metric cache-only papers
cannot report). Published anchors: InstCache 51.34% on LMSYS, SCALM +63%
vs GPTCache.

Dependencies are optional and degrade honestly:
- Embedder: `sentence-transformers/all-MiniLM-L6-v2` when installed
  (the protocol's standard encoder); otherwise a deterministic,
  dependency-free character-n-gram hashing embedder that exercises the
  entire protocol on lexical similarity. The fallback's *absolute* hit
  rates are lexical, not semantic — real semantic numbers need the real
  encoder — but the protocol, the curves, and the fixed-vs-adaptive and
  dollar-savings comparisons are identical code either way.
- Index: numpy brute-force cosine NN (exact). FAISS is a scale
  optimization, not needed for the protocol; wired in if present.
- Data: real LMSYS conversations via `--conversations <parquet>` (gated,
  needs an HF account + license accept); otherwise a synthetic set with a
  controllable near-duplicate rate so the curve is meaningful offline.

    python semantic_cache_eval.py                    # synthetic + fallback
    python semantic_cache_eval.py --conversations lmsys-chat-1m/*.parquet

AMENDMENT (2026-07-12, session 16c — declared and committed BEFORE the gated
dataset was downloaded; no result had been seen when this was frozen):
the committed protocol is computationally infeasible at full LMSYS scale —
2M+ user turns make the dense query x inserted similarity matrix ~40 TB and
a CPU encode of every turn takes hours. Feasibility amendments, none of
which change the protocol's semantics:
  1. `--sample N` (LMSYS run declared at N=200,000): a seed-deterministic
     reservoir sample of user turns drawn streaming across all parquet
     files. Sampling is uniform over the dataset, so the hit-rate estimand
     ("does a random query find a near-duplicate among the inserted set")
     is unchanged; only its sample size is bounded.
  2. Exact cosine NN is computed block-wise over recency-ordered inserted
     vectors with a running max — identical numbers to the dense matmul,
     bounded memory, and one pass yields the nearest-similarity at every
     FIFO cache size simultaneously (a FIFO cache of size K is the last-K
     suffix).
  3. The committed fixed-cache point (200 entries) stays, and a
     supplementary fixed point at 10% of inserted entries is added —
     200/100,000 alone would strawman the fixed baseline (the committed
     synthetic default was 200/2,000 = 10%).
  4. Phase 3b completion: hit rate at the 0.85 operating threshold across
     a ladder of cache sizes, with the saturating fit
     h(K) = hmax*K/(K+K_half) reported in ENTRIES (the sim's h(c) is over
     MB; the entries->MB mapping needs a bytes-per-entry estimate and is
     stated, not silently assumed).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import tempfile
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))

import stats  # noqa: E402  (research/analysis/stats.py)
from model import TIER_COST_USD_PER_REQ  # noqa: E402

# Written through stats.record_path so POLYFORGE_ANALYSIS_OUT can redirect it;
# it resolved to this directory unconditionally, so running the script
# overwrote the committed record and the gate could not compare it.
OUT = stats.record_path("SEMANTIC_CACHE.md")

# A smoke run must not be able to overwrite the published record. This script
# wrote the same document whichever way it ran, and its own docstring lists
# `python semantic_cache_eval.py` -- synthetic prompts through the lexical
# fallback embedder -- as the first usage. So the published LMSYS + MiniLM
# numbers could be replaced by numbers of an entirely different provenance by
# anyone smoke-testing the protocol, and CI had to `git checkout --` the file
# afterwards to undo exactly that. Provenance now chooses the destination, and
# CI asserts the published record was left alone instead of restoring it.
SMOKE_OUT = Path(tempfile.gettempdir()) / "SEMANTIC_CACHE_smoke.md"
THRESHOLDS = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
# A hit avoids one mid-tier model call; miss pays it. The dollar axis.
CALL_PRICE_USD = TIER_COST_USD_PER_REQ["mid"]


# --- embedders ---------------------------------------------------------
def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.clip(n, 1e-9, None)


class HashEmbedder:
    """Dependency-free char-3-gram hashing to a fixed dim, L2-normalized.
    Deterministic; lexical near-duplicates land near each other in cosine."""

    name = "hash-3gram (fallback, lexical)"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float64")
        for i, t in enumerate(texts):
            t = (t or "").lower()
            for j in range(max(0, len(t) - 2)):
                g = t[j:j + 3]
                h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=4).digest(), "big")
                out[i, h % self.dim] += 1.0
        return _l2(out)


class MiniLMEmbedder:
    """The protocol's standard encoder, used when installed."""

    name = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self):
        from sentence_transformers import SentenceTransformer

        self._m = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def encode(self, texts: list[str]) -> np.ndarray:
        return _l2(np.asarray(self._m.encode(texts, batch_size=256, show_progress_bar=False),
                              dtype="float64"))


def make_embedder():
    try:
        import sentence_transformers  # noqa: F401

        return MiniLMEmbedder()
    except Exception:
        return HashEmbedder()


# --- data --------------------------------------------------------------
def synthetic_prompts(seed: int, n: int = 4000, templates: int = 300,
                      dup_rate: float = 0.45) -> list[str]:
    """Prompts with a controllable near-duplicate rate: `dup_rate` of them
    are lexical variants of a template (what a real cache should catch),
    the rest are unique. Mirrors LMSYS's repeated-question structure."""
    rng = np.random.default_rng(seed)
    bases = [f"how do i {v} a {n_}" for v in
             ("configure", "reset", "install", "debug", "optimize", "secure")
             for n_ in (f"widget{i}" for i in range(templates // 6 + 1))][:templates]
    fillers = ("", " please", " in python", " quickly", " step by step", " on windows")
    prompts = []
    for _ in range(n):
        if rng.random() < dup_rate:
            base = bases[int(rng.integers(len(bases)))]
            prompts.append(base + fillers[int(rng.integers(len(fillers)))])
        else:
            prompts.append(f"unique query {int(rng.integers(10**9))} about topic "
                           f"{int(rng.integers(10**6))}")
    return prompts


def load_lmsys_prompts(pattern: str, sample: int | None = None, seed: int = 42) -> list[str]:
    """User turns from the parquet files; with `sample`, a seed-deterministic
    reservoir sample drawn streaming (one file in memory at a time), uniform
    over every user turn in the dataset."""
    import pandas as pd

    paths = sorted(glob.glob(pattern))
    if not paths:
        raise SystemExit(f"no parquet matched {pattern}")
    rng = np.random.default_rng(seed)
    reservoir: list[str] = []
    seen = 0
    for path in paths:
        df = pd.read_parquet(path)
        for convo in df["conversation"]:
            for msg in convo:
                if msg.get("role") != "user" or not msg.get("content"):
                    continue
                text = msg["content"]
                seen += 1
                if sample is None:
                    reservoir.append(text)
                elif len(reservoir) < sample:
                    reservoir.append(text)
                else:
                    j = int(rng.integers(seen))
                    if j < sample:
                        reservoir[j] = text
        del df
    if sample is not None:
        # Restore a uniformly random order (reservoir replacement preserves
        # uniformity of membership, not of position).
        rng.shuffle(reservoir)
        print(f"reservoir: {len(reservoir)} of {seen} user turns (seed {seed})")
    return reservoir


# --- protocol ----------------------------------------------------------
def nearest_at_sizes(queries: np.ndarray, inserted: np.ndarray,
                     sizes: list[int], block: int = 512) -> dict[int, np.ndarray]:
    """Exact cosine nearest-neighbor similarity of every query against every
    FIFO cache size in one pass. A FIFO cache of size K holds the last K
    inserted vectors, so walking inserted blocks from most recent to oldest
    with a running max gives nearest-vs-suffix at every K cut point —
    numerically identical to the dense matmul, in bounded memory."""
    sizes = sorted({min(s, len(inserted)) for s in sizes})
    running = np.full(len(queries), -1.0)
    out: dict[int, np.ndarray] = {}
    covered = 0  # how many most-recent inserted vectors the running max covers
    for size in sizes:
        while covered < size:
            step = min(block, size - covered)
            blk = inserted[len(inserted) - covered - step: len(inserted) - covered]
            np.maximum(running, (queries @ blk.T).max(axis=1), out=running)
            covered += step
        out[size] = running.copy()
    return out


def hit_rate_curve(vecs: np.ndarray, cache_size: int | None) -> dict[float, float]:
    """Insert first half, query second half; a query hits if its nearest
    inserted neighbor's cosine >= threshold. `cache_size` caps inserted
    entries with FIFO eviction (None = unbounded / fully adaptive)."""
    half = len(vecs) // 2
    size = half if cache_size is None else min(cache_size, half)
    nearest = nearest_at_sizes(vecs[half:], vecs[:half], [size])[size]
    return {th: float((nearest >= th).mean()) for th in THRESHOLDS}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conversations", default=None, help="real LMSYS parquet glob")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None,
                    help="where to write the record; without it the published "
                         "record is written ONLY by a published-provenance run "
                         "(real LMSYS conversations through the MiniLM encoder)")
    ap.add_argument("--fixed-cache", type=int, default=200,
                    help="fixed cache entry cap (the naive baseline)")
    ap.add_argument("--sample", type=int, default=None,
                    help="seeded reservoir sample of user turns (LMSYS run "
                         "declared at 200000 in the amendment)")
    args = ap.parse_args()

    if args.conversations:
        prompts = load_lmsys_prompts(args.conversations, args.sample, args.seed)
        sampled = f", reservoir sample seed {args.seed}" if args.sample else ""
        source = f"LMSYS-Chat-1M ({len(prompts)} user turns{sampled})"
    else:
        prompts = synthetic_prompts(args.seed)
        source = f"synthetic ({len(prompts)} prompts, controllable dup-rate)"

    emb = make_embedder()
    vecs = emb.encode(prompts)

    # One recency-ordered pass covers the committed fixed point, the
    # supplementary 10%-of-inserted point, the h(K) ladder, and unbounded.
    half = len(vecs) // 2
    supplementary = max(1, half // 10)
    ladder = sorted({s for s in (500, 2000, 8000, 32000, 64000) if s < half}
                    | {half, min(args.fixed_cache, half), supplementary})
    nearest = nearest_at_sizes(vecs[half:], vecs[:half], ladder)

    def curve(size: int) -> dict[float, float]:
        return {th: float((nearest[size] >= th).mean()) for th in THRESHOLDS}

    adaptive = curve(half)                            # demand-sized
    fixed = curve(min(args.fixed_cache, half))        # committed naive fixed
    fixed10 = curve(supplementary)                    # supplementary, 10% of inserted

    lines = ["# Semantic-cache hit-rate protocol (v2 Phase 3b)", ""]
    w = lines.append
    w(f"Source: {source}. Embedder: `{emb.name}`. Index: exact cosine NN "
      "(numpy). Protocol: insert first half, query second half, sweep the "
      "similarity threshold.")
    w("")
    if isinstance(emb, HashEmbedder):
        w("> **Fallback embedder active** (sentence-transformers not "
          "installed): absolute hit rates below are *lexical*, not semantic. "
          "The protocol, curves, and fixed-vs-adaptive/dollar comparisons are "
          "the exact code the real run uses — install sentence-transformers "
          "and pass `--conversations` for the headline semantic numbers "
          "(anchors: InstCache 51.34%, SCALM +63% vs GPTCache).")
        w("")
    elif not args.conversations:
        w("> **Real encoder on synthetic prompts**: the synthetic set's "
          "duplicate structure was designed to exercise *lexical* "
          "discrimination; a semantic encoder correctly collapses its "
          "template families, so hit rates saturate near 1.0. This run "
          "verifies the full semantic pipeline end-to-end; the absolute "
          "numbers comparable to the published anchors (InstCache 51.34%, "
          "SCALM +63% vs GPTCache) require `--conversations` on the real "
          "LMSYS-Chat-1M parquet (gated: needs an HF account + accept).")
        w("")
    w(f"Dollar model: a hit avoids one mid-tier call at "
      f"${CALL_PRICE_USD:.4f}; savings = hit-rate × price × queries.")
    w("")
    w(f"| threshold | adaptive hit rate | fixed (≤{args.fixed_cache}) hit rate | "
      "adaptive $ saved/1k queries |")
    w("|---|---|---|---|")
    for th in THRESHOLDS:
        saved = adaptive[th] * CALL_PRICE_USD * 1000
        w(f"| {th:.2f} | {adaptive[th]:.3f} | {fixed[th]:.3f} | ${saved:.3f} |")
    w("")
    # Report at a representative operating threshold (0.85, the usual knee).
    op = 0.85
    gain = (adaptive[op] - fixed[op]) / fixed[op] if fixed[op] else 0.0
    w(f"**At threshold {op}: adaptive sizing hits {adaptive[op]:.1%} vs the "
      f"fixed cache's {fixed[op]:.1%} ({gain:+.0%}).** Adaptive keeps the whole "
      "working set the demand justifies; the fixed cache evicts recurring "
      "prompts it should have kept. The dollar column is the metric a "
      "cache-only paper cannot report: PolyForge prices each avoided call at "
      "its model tier, so a hit on an expensive tier saves more than a hit on "
      "a cheap one — hit rate and savings are not the same axis.")
    w("")
    if args.sample:
        w(f"Amendment (declared pre-run, see script docstring): reservoir "
          f"sample of {args.sample} user turns; supplementary fixed cache at "
          f"10% of inserted ({supplementary} entries) so the fixed baseline "
          f"is not a strawman at this scale: hit rate {fixed10[op]:.1%} at "
          f"threshold {op} (vs adaptive {adaptive[op]:.1%}).")
        w("")
    # Phase 3b completion: h(K) across the FIFO ladder at the operating
    # threshold, with the sim's saturating form fitted in ENTRIES.
    hs = {k: float((nearest[k] >= op).mean()) for k in ladder}
    w(f"## h(cache size) at threshold {op} — Phase 3b empirical curve")
    w("")
    w("| cache entries | hit rate |")
    w("|---|---|")
    for k in sorted(hs):
        w(f"| {k} | {hs[k]:.3f} |")
    w("")
    try:
        from scipy.optimize import curve_fit

        ks = np.array(sorted(hs), dtype="float64")
        ys = np.array([hs[k] for k in sorted(hs)])
        (hmax, khalf), _ = curve_fit(
            lambda k, m, h: m * k / (k + h), ks, ys,
            p0=[max(ys.max(), 1e-3), max(float(ks.mean()), 1.0)], maxfev=10000)
        w(f"**Saturating fit h(K) = hmax·K/(K+K_half): hmax = {hmax:.3f}, "
          f"K_half = {khalf:.0f} entries.** The sim's h(c) uses the same form "
          "over MB (CACHE_HIT_MAX=0.85, CACHE_HALF_MB=256); at ~3 KB per "
          "entry (384-d float32 embedding + prompt text + metadata), "
          f"K_half ≈ {khalf * 3 / 1024:.0f} MB equivalent. Adopting empirical "
          "constants in model.py would be a new pre-registered experiment; "
          "the committed matrices stay bit-reproducible (see session 16b "
          "precedent in docs/archive/SESSION_LOG.md).")
    except Exception as exc:  # fit is reporting, not gating
        w(f"(saturating fit did not converge: {exc})")
    w("")
    w("Reproduce: `python semantic_cache_eval.py` (synthetic+fallback) or "
      "`--conversations lmsys-chat-1m/*.parquet` with sentence-transformers "
      "installed. The LMSYS ETL that normalizes the same dataset for demand "
      "replay is `research/traces/etl_lmsys_chat1m.py`.")

    published = bool(args.conversations) and isinstance(emb, MiniLMEmbedder)
    destination = Path(args.out) if args.out else (OUT if published else SMOKE_OUT)
    destination.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    print(f"wrote {destination}: adaptive@0.85={adaptive[0.85]:.3f} "
          f"fixed@0.85={fixed[0.85]:.3f} embedder={emb.name}")
    if not published and not args.out:
        print(f"provenance is not the published one (data="
              f"{'LMSYS' if args.conversations else 'synthetic'}, "
              f"embedder={emb.name}), so {OUT.name} was left untouched.")


if __name__ == "__main__":
    main()
