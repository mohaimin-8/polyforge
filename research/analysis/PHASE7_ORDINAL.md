# Phase 7 ordinal figure — sim ranking vs live ranking

Protocol frozen in `phase7_ordinal.py` before the live slice finished. Ordinal reading only (ground rule 4): per cell, does the substrate-internal winner agree? Absolutes are never compared across substrates.

## Cell: ai_cacheable

| substrate | system | J mean [min..max] | cost $ mean | violation mean |
|---|---|---|---|---|
| sim | jcac | 0.596 [0.593..0.601] | 5.66 | 0.0006 |
| sim | hpa | 0.921 [0.917..0.924] | 8.77 | 0.0000 |
| live | jcac | 0.739 [0.738..0.739] | 7.03 | 0.0000 |
| live | hpa | 0.737 [0.735..0.740] | 7.02 | 0.0000 |

- **J winner** — sim: `jcac`, live: `hpa` -> **DISAGREE**
- **cost winner** — sim: `jcac`, live: `hpa` -> **DISAGREE**
- **violation winner** — sim: `hpa`, live: `(tie)` -> **TIE — no ordinal reading**

## Cell: crud_bursty

| substrate | system | J mean [min..max] | cost $ mean | violation mean |
|---|---|---|---|---|
| sim | jcac | 0.040 [0.040..0.040] | 0.38 | 0.0000 |
| sim | hpa | 0.136 [0.136..0.136] | 0.30 | 0.0417 |
| live | jcac | 0.027 [0.027..0.027] | 0.26 | 0.0000 |
| live | hpa | 0.025 [0.023..0.027] | 0.24 | 0.0000 |

- **J winner** — sim: `jcac`, live: `hpa` -> **DISAGREE**
- **cost winner** — sim: `hpa`, live: `hpa` -> **AGREE**
- **violation winner** — sim: `jcac`, live: `(tie)` -> **TIE — no ordinal reading**

## Verdict (primary reading = J winner per cell)

- J: ai_cacheable: DISAGREE; crud_bursty: DISAGREE
- cost: ai_cacheable: DISAGREE; crud_bursty: AGREE
- violation: ai_cacheable: TIE — no ordinal reading; crud_bursty: TIE — no ordinal reading

Frozen caption (PHASE7_JCAC_PLAN.md step 3):

> Live ordinal check on a kind cluster: does the simulator's jcac-vs-HPA ranking on J/cost/violation reproduce under a real scheduler, real HPA, and real load? Absolutes are not comparable across substrates and are not compared. The live data plane burns fixed CPU per request kind, so the cache-size and model-tier knobs are inert here: this figure validates the replica-control projection of the joint controller. The cache knob's realism is carried by the LMSYS protocol (SEMANTIC_CACHE.md, CACHE_PRECISION.md), the tier knob's by the GPU tier bench (TIER_BENCH.md).
