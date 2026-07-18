# Exploratory: all three structural forms together — as measured (not a headline)

Declared in `PREREG_MIXTURE_P95.md` ("exploratory arm, run only after all
three Wave 5 primaries; its reading informs future-work text, not the
headline"). Database `raw_sim_structreal.duckdb`, 1,800/1,800 valid,
validation green, spot-check zero drift. **No hypotheses were registered
for this arm; nothing here is confirmatory.** Pairing and J follow the
standard machinery; p-values are shown for scale only.

World: measured latency model (a = 0.86, tail 1.6909·(1−ρ)^−0.1303)
**and** true mixture-percentile ai_p95 **and** tier-scaled work units
(1.516× mid, 16.64× large) — every Wave 5 correction at once, i.e. the
most structurally hostile version of the simulator the calibration
supports.

## Descriptive reading

| baseline | J paired diff | dz | agg ΔJ | agg Δcost | v1 agg ΔJ |
|---|---|---|---|---|---|
| hpa | −0.1359 | −0.92 | −19.6% | −29.3% | −27.5% |
| keda | −0.1528 | −1.04 | −21.5% | −36.0% | −30.8% |
| firm | −0.2154 | −1.28 | −27.8% | −32.9% | −38.4% |
| static | −2.5271 | −1.07 | −81.9% | −90.9% | −86.7% |
| gptcache | −12.9025 | −0.86 | −95.9% | −97.9% | −94.7% |

System means: jcac cost 2.62 / violation 0.1295 / Jain 0.9517; keda
violation 0.1293 (parity within noise), hpa 0.1395; gptcache is the
composite casualty of all three corrections at once (violation 0.214,
Jain 0.811, cost 123).

## What it says (future-work framing only)

The three corrections compound in the expected direction — every jcac
margin is at its narrowest here (J −19.6% vs hpa, from −27.5% in v1) —
and none closes. Violation parity with the best tuned reactive scaler
holds in the combined world. Consistent with the three isolating arms:
the joint controller's advantage does not live in any one modeling
kindness, and the structurally-hostile corner is the right *starting
model* for any future v2 simulator rather than a threat to the current
claims. A confirmatory adoption of this combined world (with re-tuned
baselines) is the named future-work experiment if a v2 evaluation
program is ever opened.
