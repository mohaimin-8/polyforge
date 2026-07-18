# Pre-registration: measured latency-model adoption rerun (Wave 5, closes the P95/exponent half of DEFENSE_QA #16's asymmetry)

Registered 2026-07-18 (session 24). Committed and pushed **before** any
campaign run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17.

## Question

The Phase 6 calibration measured two latency-model discrepancies
(`CALIBRATION.md`): the congestion exponent (fitted a = 0.86 vs the
asserted 1.0) and the p95/mean tail factor (measured 1.59–2.41 **rising
with ρ** vs the asserted flat 1.4). The Wave 2 program adopted the other
two measured discrepancies (tier prices, cache curve) in pre-registered
reruns but never these two — an asymmetry with a direction: the soft
exponent errs *against* lean postures, but the flat tail errs *for* them
(the sim understates tails everywhere, most in overload, which is where
lean configurations live). The "calibration errs against us" line is
therefore only half-closed. This experiment adopts **both** measured
elements together — they were measured together, on the same substrate,
and adopting only the favorable one would repeat the asymmetry in
reverse.

## Design (frozen)

- Experiment: `eval/experiments/matrix_lm.yaml` — exact mirror of the v1
  headline `full.yaml` (6 systems × 5 × 4 × 3 × 5 = 1,800 sim runs) with
  `model_form: {congestion_exponent: 0.86, p95_tail_f0: 1.6909,
  p95_tail_b: 0.1303}`.
- Constant derivation, fixed here: a = 0.86 is the committed CALIBRATION.md
  fit. The tail form F(ρ) = f0·(1−min(ρ, 0.95))^(−b) is fitted by least
  squares in log space on the committed `congestion_runs.csv` per-level
  p95/mean column (`fit_p95_factor.py`, output `p95_tail_fit.json`):
  f0 = 1.6909, b = 0.1303, log-space R² 0.665 (the flat 1.4 scores −8.0 on
  the same points). The clamp at SATURATION_RHO mirrors the congestion
  curve's overload device; the fit input is published calibration data, so
  freezing it here is outcome-free.
- Mechanism: `model.set_model_form` (this session; default path verified
  bit-identical by 6/6 + 4/4 zero-drift spot-check replays of the
  committed v1 and HK matrices, and by the `KNOWN_FULL_RUN` identity
  guard). The form applies to the **world and every controller's beliefs
  identically** — controllers plan through the same `evaluate_step` the
  scorer uses. No controller is retuned: the W34 tuned parameters stand,
  which is the same no-retuning disclosure as PREREG_TIER_RATIO (a
  baseline whose tuning was fitted to the flat-tail world keeps it; so
  does jcac).
- Only a mechanics smoke (non-campaign cell IDs, validity/timing only)
  preceded this registration; no campaign cell has been executed.

## Hypotheses (frozen)

Pairing/tests as the headline (300 per-cell pairs per baseline on
CELL_KEYS, one-sample t on paired differences, alpha 0.01), mechanized in
`analysis_econ.py lm`.

- **LM-H1 (primary):** under the measured latency model, jcac beats
  **each** of hpa, keda, firm, static, gptcache on composite J (p < 0.01).
- **LM-H2 (secondary):** jcac remains the cost winner against each
  baseline (paired total_cost_usd, p < 0.01).
- **LM-H3 (violation non-inferiority, the reason this arm exists):** the
  measured tail is ≥ 1.59 everywhere the flat form said 1.4, so lean
  postures must violate more. PASS per baseline b ∈ {hpa, keda} iff the
  one-sided 99% upper confidence bound of the paired mean_violation diff
  (jcac − b) stays within (v1 paired diff + 0.02), the v1 reference
  recomputed live from the committed `raw_sim.duckdb` (v1 paired diffs at
  registration: hpa −0.0045, keda +0.0010). A FAIL here means the
  SLO-parity claim does not survive the measured tail and the thesis text
  must say so.
- **Declared mechanism expectations** (directional, not hypotheses):
  absolute violations rise for every system (the world's tails got
  heavier); the softened exponent partially offsets below saturation;
  lean systems (jcac, tuned reactives) are pressured more than
  over-provisioned postures (static).

## Outcome handling and stopping rule

As PREREG_HK_ADOPTION: results to `RESULTS_LM_ADOPTION.md` as measured
(including any H1/H3 failure, stated verbatim); one execution of
`raw_sim_lm.duckdb`, valid only at 1,800/1,800; crash-resume allowed; no
widening; no third latency-model variant inside this thesis if the
outcome disappoints.
