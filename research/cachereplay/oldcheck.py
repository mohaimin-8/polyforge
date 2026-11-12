"""WP6 check: at the old settings the replay reproduces the published LMSYS
numbers (research/analysis/SEMANTIC_CACHE.md).

The old settings are the old script's own, called from
research/analysis/semantic_cache_eval.py rather than re-implemented: its loader
(reservoir sample of all user turns, seed 42), its encoder wrapper (MiniLM,
float64 vectors) and its protocol (insert the first half, query the second
half; a cache of size K is the last K inserted). Two comparisons:
  (a) the old functions' hit rates against the published table;
  (b) the WP6 engine in the same protocol (cachesim.replay_insert_then_query,
      through the same TenantCache that replays the gateway) against the old
      functions, query by query.

    python oldcheck.py --preset smoke   # 600-turn reservoir from a 1,000-conversation slice; (b) only
    python oldcheck.py --preset full    # the published 200,000-turn reservoir; (a) and (b)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from cachesim import NeighborIndex, replay_insert_then_query
from streams import DATA, LMSYS_GLOB

HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parents[0] / "analysis"
PUBLISHED = ANALYSIS / "SEMANTIC_CACHE.md"
PRESETS = {"smoke": {"sample": 600, "slice_conversations": 1000, "threads": 2},
           "full": {"sample": 200_000, "slice_conversations": None, "threads": None}}


def old_module():
    if str(ANALYSIS) not in sys.path:
        sys.path.append(str(ANALYSIS))
    import semantic_cache_eval  # noqa: PLC0415  (the old study's script)
    return semantic_cache_eval


def published_table(path: Path = PUBLISHED) -> dict:
    text = path.read_text(encoding="utf-8")
    thr = re.findall(r"^\| (0\.\d\d) \| (0\.\d+) \| (0\.\d+) \| \$[\d.]+ \|$", text, re.M)
    ladder = re.findall(r"^\| (\d+) \| (0\.\d+) \|$", text, re.M)
    supp = re.search(r"10% of inserted \((\d+) entries\)[^:]*: hit rate ([\d.]+)%", text)
    fit = re.search(r"hmax = ([\d.]+), K_half = (\d+) entries", text)
    if not thr or not ladder:
        raise SystemExit(f"could not parse {path}")
    return {"adaptive": {t: float(a) for t, a, _ in thr}, "fixed200": {t: float(f) for t, _, f in thr},
            "ladder_at_0.85": {int(k): float(h) for k, h in ladder},
            "supplementary": {"entries": int(supp.group(1)), "hit_rate_at_0.85": float(supp.group(2)) / 100}
            if supp else None,
            "fit": {"hmax": float(fit.group(1)), "k_half": int(fit.group(2))} if fit else None}


def slice_source(n_conversations: int) -> str:
    """The first n conversations of the first parquet file (conversation
    column only), for the smoke; stays under data/ (git-ignored)."""
    src = sorted(Path(p) for p in Path(LMSYS_GLOB).parent.glob("*.parquet"))[0]
    dest = DATA / "oldcheck" / f"slice{n_conversations}.parquet"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        table = pq.ParquetFile(src).read_row_groups([0, 1], columns=["conversation"]).slice(0, n_conversations)
        pq.write_table(table, dest)
    return str(dest)


def embed_cached(old, prompts: list[str], tag: str, threads: int | None) -> tuple[np.ndarray, float]:
    """The old encoder wrapper's float64 vectors, cached by a hash of the texts."""
    digest = hashlib.sha256("\x00".join(prompts).encode("utf-8")).hexdigest()[:16]
    path = DATA / "oldcheck" / f"{tag}.{len(prompts)}.{digest}.f64.npy"
    if path.exists():
        return np.load(path, allow_pickle=False), 0.0
    if threads:
        import torch  # noqa: PLC0415
        torch.set_num_threads(threads)
    t0 = time.perf_counter()
    vecs = old.MiniLMEmbedder().encode(prompts)
    seconds = time.perf_counter() - t0
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, vecs, allow_pickle=False)
    return vecs, seconds


def old_ladder(half: int, fixed_cache: int = 200) -> list[int]:
    """semantic_cache_eval.main's ladder, verbatim in effect."""
    supplementary = max(1, half // 10)
    return sorted({s for s in (500, 2000, 8000, 32000, 64000) if s < half}
                  | {half, min(fixed_cache, half), supplementary})


def compare_engine(old, vecs: np.ndarray, ladder: list[int]) -> tuple[dict, dict]:
    half = len(vecs) // 2
    want = old.nearest_at_sizes(vecs[half:], vecs[:half], ladder)
    t0 = time.perf_counter()
    nb = NeighborIndex(vecs, k=64, history_end=np.full(len(vecs), half), rows=np.arange(half, len(vecs)))
    got = replay_insert_then_query(vecs, ladder, neighbors=nb)
    seconds = time.perf_counter() - t0
    diffs = {}
    for size in want:
        diffs[size] = {"max_abs_score_diff": float(np.max(np.abs(got[size] - want[size]))),
                       "hit_count_diffs": {f"{t:.2f}": int((got[size] >= t).sum() - (want[size] >= t).sum())
                                           for t in old.THRESHOLDS}}
    return want, {"engine_seconds": seconds, "by_size": diffs}


def against_published(old, nearest: dict, half: int, published: dict) -> dict:
    rates = {size: {f"{t:.2f}": float((nearest[size] >= t).mean()) for t in old.THRESHOLDS} for size in nearest}
    rows = []
    for t, pub in published["adaptive"].items():
        rows.append(("adaptive", t, pub, rates[half][t]))
        rows.append(("fixed200", t, published["fixed200"][t], rates[min(200, half)][t]))
    for k, pub in published["ladder_at_0.85"].items():
        if k in rates:
            rows.append((f"ladder K={k}", "0.85", pub, rates[k]["0.85"]))
    worst = max(abs(round(new, 3) - pub) for _, _, pub, new in rows)
    return {"rows": [{"what": w, "tau": t, "published": p, "reproduced": n} for w, t, p, n in rows],
            "max_abs_diff_at_3dp": worst, "reproduced": worst <= 0.001}


def markdown(r: dict) -> str:
    L = ["# WP6 check: the old protocol reproduced", "",
         f"Sample: {r['prompts']} user turns ({r['source']}), reservoir seed 42, via the old script's loader; "
         f"encoder: the old script's MiniLM wrapper ({r['embed_seconds']:.1f} s to embed, 0 = cached).",
         "", "## (b) WP6 engine vs the old functions, query by query", "",
         "| cache size K | max abs score difference | hit-count differences at the six thresholds |",
         "|---|---|---|"]
    for size, d in r["engine"]["by_size"].items():
        L.append(f"| {size} | {d['max_abs_score_diff']:.2e} | {sorted(set(d['hit_count_diffs'].values()))} |")
    L += ["", f"Engine identical to the old protocol: **{r['engine_identical']}**."]
    pub = r.get("published")
    L += ["", "## (a) old functions vs the published table (SEMANTIC_CACHE.md)", ""]
    if pub is None:
        L.append("Not applicable to this sample (the published numbers are for the 200,000-turn reservoir; "
                 "run `--preset full`).")
    else:
        L += ["| row | tau | published | reproduced |", "|---|---|---|---|"]
        L += [f"| {x['what']} | {x['tau']} | {x['published']:.3f} | {x['reproduced']:.3f} |" for x in pub["rows"]]
        L += ["", f"Largest difference at three decimals: {pub['max_abs_diff_at_3dp']:.3f}; reproduced: "
              f"**{pub['reproduced']}**."]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=sorted(PRESETS), default="smoke")
    ap.add_argument("--out", help="output directory (default out/<preset>)")
    args = ap.parse_args()
    cfg = PRESETS[args.preset]
    out = Path(args.out) if args.out else HERE / "out" / args.preset
    out.mkdir(parents=True, exist_ok=True)
    old = old_module()
    pattern = slice_source(cfg["slice_conversations"]) if cfg["slice_conversations"] else LMSYS_GLOB
    prompts = old.load_lmsys_prompts(pattern, cfg["sample"], 42)
    vecs, seconds = embed_cached(old, prompts, f"reservoir_{args.preset}", cfg["threads"])
    half = len(vecs) // 2
    ladder = old_ladder(half)
    nearest, engine = compare_engine(old, vecs, ladder)
    identical = all(set(d["hit_count_diffs"].values()) == {0} for d in engine["by_size"].values())
    result = {"preset": args.preset, "prompts": len(prompts), "embed_seconds": seconds,
              "source": "first 1,000 conversations of file 0" if cfg["slice_conversations"]
              else "all six LMSYS parquet files", "ladder": ladder, "engine": engine,
              "engine_identical": identical,
              "published": against_published(old, nearest, half, published_table())
              if args.preset == "full" else None}
    (out / "old_check.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (out / "OLD_CHECK.md").write_text(markdown(result), encoding="utf-8")
    print(f"wrote {out / 'OLD_CHECK.md'}: engine identical {identical}"
          + (f", published reproduced {result['published']['reproduced']}" if result["published"] else ""))


if __name__ == "__main__":
    main()
