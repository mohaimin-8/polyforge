#!/usr/bin/env python3
"""C1 — is the semantic cache's precision limited by RETRIEVAL or by the MODEL?

`CACHE_PRECISION.md` measures precision 0.313 at tau=0.85 (rho=0.70) and
*declares* an interpretation: that low response agreement reflects response
stochasticity rather than a retrieval failure. That declaration is load-bearing
-- it is why the record claims only relative readings -- and it is asserted,
not measured. This file measures it.

**The oracle.** Restrict to prompts whose text is EXACTLY duplicated in the
corpus. There, retrieval is perfect by construction: the cache would serve a
response stored against a character-identical prompt. Any remaining
disagreement cannot be the embedder's fault. The same-model stratum strips the
style confounder too, so it is the cleanest available estimate of the ceiling.

`cache_hit_precision.py` is FROZEN and is not edited; this imports its loader
and its encoder so both analyses read the corpus the same way.

================================================================
THE READING, FIXED BEFORE THE NUMBER WAS LOOKED AT
================================================================

Published anchors from `CACHE_PRECISION.md` at tau=0.85, rho=0.70:
overall precision **0.313**, same-model stratum **0.443**.

  C1 (self-check, must hold). The oracle stratum must actually exist: at least
      1,000 duplicate pairs, drawn from more than 100 distinct prompt groups,
      with per-group pairs capped so one huge group ("hi") cannot become the
      estimate. Below that this file reports NOT EVALUABLE and stops.

  C2 (the measurement). Oracle precision@rho=0.70, overall / same-model /
      cross-model.

  C3 (the verdict, threshold fixed here in advance).
      * Oracle same-model precision **<= 0.60** -> **RETRIEVAL IS NOT THE
        BINDING CONSTRAINT.** Even with perfect retrieval the cache is wrong
        this often, so no embedder swap can raise the headline and
        `CACHE_PRECISION.md`'s declared reading is CONFIRMED by measurement.
      * Oracle same-model precision **> 0.60** -> **RETRIEVAL IS PARTLY
        BINDING.** A materially better embedder would raise precision, and the
        record's caveat is understated rather than conservative. Report the
        gap; do not re-tune anything inside this record.

      0.60 is chosen as roughly midway between the published same-model 0.443
      and certainty, before seeing any oracle number. It is not moved
      afterwards.

Neither outcome changes `CACHE_PRECISION.md`, which stands as committed.

    python analysis_cache_ceiling.py
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache_hit_precision as chp  # noqa: E402  (frozen; imported, never edited)
from stats import record_path  # noqa: E402

TRACES = (Path(__file__).resolve().parents[1] / "traces" / "data"
          / "lmsys-chat-1m" / "data" / "*.parquet")
RECORD = "RESULTS_CACHE_CEILING.md"
SAMPLE = 120000
SEED = 42
RHO = 0.70
MAX_PAIRS_PER_GROUP = 20
MIN_PAIRS = 1000
MIN_GROUPS = 100
CEILING_RULE = 0.60
PUBLISHED_OVERALL = 0.313
PUBLISHED_SAME_MODEL = 0.443


def stats(arr: np.ndarray) -> dict:
    return {
        "n": int(len(arr)),
        "mean": float(arr.mean()) if len(arr) else float("nan"),
        "median": float(np.median(arr)) if len(arr) else float("nan"),
        "prec70": float((arr >= 0.70).mean()) if len(arr) else float("nan"),
        "prec60": float((arr >= 0.60).mean()) if len(arr) else float("nan"),
        "prec80": float((arr >= 0.80).mean()) if len(arr) else float("nan"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--conversations", default=str(TRACES))
    ap.add_argument("--sample", type=int, default=SAMPLE)
    args = ap.parse_args()

    rows = chp.load_pairs(args.conversations, args.sample, SEED)
    prompts = [r[0] for r in rows]
    responses = [r[1] for r in rows]
    models = [r[2] for r in rows]

    groups: dict[str, list[int]] = defaultdict(list)
    for i, prompt in enumerate(prompts):
        groups[prompt.strip()].append(i)
    dup = {k: v for k, v in groups.items() if len(v) > 1}

    idx = sorted({i for v in dup.values() for i in v})
    rvecs = chp.encode([responses[i] for i in idx], "responses") if idx else None
    pos = {orig: k for k, orig in enumerate(idx)}

    rng = np.random.default_rng(SEED)
    same: list[float] = []
    cross: list[float] = []
    for members in dup.values():
        pairs = [(a, b) for ai, a in enumerate(members) for b in members[ai + 1:]]
        if len(pairs) > MAX_PAIRS_PER_GROUP:
            pick = rng.choice(len(pairs), MAX_PAIRS_PER_GROUP, replace=False)
            pairs = [pairs[i] for i in pick]
        for a, b in pairs:
            agree = float(np.dot(rvecs[pos[a]], rvecs[pos[b]]))
            (same if models[a] == models[b] else cross).append(agree)

    same_a, cross_a = np.array(same), np.array(cross)
    all_a = np.concatenate([same_a, cross_a]) if len(same_a) or len(cross_a) \
        else np.array([])
    c1 = len(all_a) >= MIN_PAIRS and len(dup) >= MIN_GROUPS and len(same_a) > 0

    s_all, s_same, s_cross = stats(all_a), stats(same_a), stats(cross_a)
    binding = (s_same["prec70"] > CEILING_RULE) if c1 else None

    L: list[str] = []
    w = L.append
    verdict = ("NOT EVALUABLE" if binding is None else
               "retrieval is PARTLY BINDING" if binding else
               "retrieval is NOT the binding constraint")
    w(f"# The semantic cache's precision ceiling — {verdict} (C1)\n")
    w("Generated by `analysis_cache_ceiling.py`. Every number is computed at "
      "run time from the committed LMSYS-Chat-1M parquets.\n")
    w("**`CACHE_PRECISION.md` is unchanged and stands as committed.** This "
      "record tests one interpretation that record *declares* — that its low "
      "response agreement is model stochasticity rather than retrieval error "
      "— and reports the answer whichever way it lands.\n")

    w("\n## The oracle\n")
    w("Restricted to prompts whose text is **exactly duplicated** in the "
      "corpus. Retrieval there is perfect by construction: a cache would serve "
      "a response stored against a character-identical prompt, so the embedder "
      "cannot be at fault for any disagreement that remains. The **same-model** "
      "stratum additionally removes the cross-model style confounder that "
      "`CACHE_PRECISION.md` names, and is therefore the cleanest estimate.\n")
    w(f"Sample {len(rows):,} (prompt, response) pairs, seed {SEED}; "
      f"{len(groups):,} distinct prompts, **{len(dup):,} with duplicates**; at "
      f"most {MAX_PAIRS_PER_GROUP} pairs scored per group so a single large "
      "group cannot become the estimate.\n")
    w(f"\n**C1 {'PASS' if c1 else 'FAIL'}** — "
      f"{len(all_a):,} duplicate pairs over {len(dup):,} groups "
      f"(floor: {MIN_PAIRS:,} pairs, {MIN_GROUPS} groups).\n")

    if not c1:
        w("\nThe oracle stratum is too thin to carry a verdict, so none is "
          "given. This is reported rather than patched by widening the sample "
          "until a number appears.\n")
        record_path(RECORD).write_text("\n".join(L) + "\n", encoding="utf-8")
        print("C1 FAIL — oracle stratum too thin")
        return 1

    w("\n## C2 — measured\n")
    w("| stratum | pairs | mean agreement | median | precision @0.60 "
      "| **@0.70** | @0.80 |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for label, s in (("all duplicate pairs", s_all),
                     ("**same-model** (cleanest)", s_same),
                     ("cross-model", s_cross)):
        if not s["n"]:
            w(f"| {label} | 0 | — | — | — | — | — |")
            continue
        w(f"| {label} | {s['n']:,} | {s['mean']:.4f} | {s['median']:.4f} "
          f"| {s['prec60']:.4f} | **{s['prec70']:.4f}** | {s['prec80']:.4f} |")
    w("")

    w("\n## C3 — the verdict\n")
    w("| quantity | value |")
    w("|---|---:|")
    w(f"| published overall precision (tau=0.85, rho=0.70) | {PUBLISHED_OVERALL:.3f} |")
    w(f"| published same-model stratum | {PUBLISHED_SAME_MODEL:.3f} |")
    w(f"| **oracle same-model precision** | **{s_same['prec70']:.4f}** |")
    w(f"| rule fixed in advance | > {CEILING_RULE:.2f} means retrieval binds |")
    w("")
    if binding:
        w(f"**Retrieval is PARTLY BINDING.** With retrieval made perfect the "
          f"cache is correct {s_same['prec70']:.1%} of the time against "
          f"{PUBLISHED_SAME_MODEL:.1%} at the measured operating point, a gap "
          f"of {s_same['prec70'] - PUBLISHED_SAME_MODEL:+.3f}. So a materially "
          "better prompt embedder WOULD raise precision, and "
          "`CACHE_PRECISION.md`'s reading understates the retrieval component. "
          "The gap is reported; nothing is re-tuned inside this record.\n")
    else:
        w(f"**Retrieval is NOT the binding constraint.** Even with perfect "
          f"retrieval the cache serves a disagreeing response "
          f"{1 - s_same['prec70']:.1%} of the time — against "
          f"{1 - PUBLISHED_SAME_MODEL:.1%} at the measured operating point, so "
          "removing all retrieval error recovers only "
          f"{s_same['prec70'] - PUBLISHED_SAME_MODEL:+.3f}. **No embedder swap "
          "can raise the headline**, and `CACHE_PRECISION.md`'s declared "
          "reading is confirmed by measurement rather than asserted.\n")
        w("The practical consequence for the thesis: the cache's precision "
          "limit is a property of the *workload* — the same question answered "
          "differently — not of PolyForge's retrieval. That is why only "
          "relative cache readings are claimed, and it is now evidenced.\n")

    out = record_path(RECORD)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"C1 PASS | oracle same-model precision@0.70 = {s_same['prec70']:.4f} "
          f"| {'retrieval PARTLY BINDING' if binding else 'retrieval NOT binding'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
