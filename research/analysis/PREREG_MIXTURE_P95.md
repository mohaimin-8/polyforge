# Pre-registration: mixture-percentile rerun (Wave 5, closes the "your p95 is a rescaled mean" gap)

Registered 2026-07-18 (session 24). Committed and pushed **before** any
campaign run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17.

## Question

The published model computes `ai_p95` as a traffic-weighted **mean** over
the hit branch (20 ms) and the miss branches (hundreds–thousands of ms),
times a flat 1.4 — a statistic that is not the 95th percentile of
anything. For a bimodal mixture with hit share h, the true p95 sits in
the miss branch until h exceeds 0.95, which realized hit rates never
reach; a real percentile therefore gives the cache **no tail credit**,
while the mean-based form credits it linearly in hit share. Since the
violation term — and through it the composite J — is built on this
statistic, the mean-based form structurally inflates the cache knob's SLO
value, which is load-bearing for the jointness story and invisible to the
live ordinal check (the live cache knob is inert there). This experiment
recomputes the entire headline matrix with `ai_p95` as the true mixture
percentile.

## Design (frozen)

- Experiment: `eval/experiments/matrix_mixp95.yaml` — exact mirror of the
  v1 headline `full.yaml` (1,800 sim runs) with
  `model_form: {mixture_p95: 1}`.
- Form, fixed here: each miss branch is lognormal with its published mean
  (tier base latency × congestion) and p95/mean = the active tail factor
  (the published flat 1.4 in this arm — the latency *level* model is
  deliberately unchanged so this arm isolates the aggregation question
  from PREREG_LM_ADOPTION's level question); cache hits are a point mass
  at 20 ms; `ai_p95` is the 0.95 quantile of the weighted mixture, solved
  by fixed-count bisection in log space (bit-deterministic replays).
  CRUD stays single-branch (its form was never a mixture). Cost
  accounting is untouched — this arm moves only the latency statistic.
- Mechanism, no-retuning disclosure, and bit-identity evidence as
  PREREG_LM_ADOPTION (same session, same zero-drift spot-checks). One
  declared consequence: reactive tier rules gated on `ai_p95` vs target
  (layered/gptcache tier-up, and the SLO scorer itself) now see a
  *percentile* that barely responds to cache growth; postures that relied
  on the mean-based credit will show it. That is the measured world, not
  a bug.
- Only a mechanics smoke (non-campaign cell IDs, validity/timing only)
  preceded this registration; no campaign cell has been executed.

## Hypotheses (frozen)

Pairing/tests as the headline (300 per-cell pairs per baseline,
one-sample t, alpha 0.01), mechanized in `analysis_econ.py mixp95`.

- **MX-H1 (primary):** under the mixture percentile, jcac beats **each**
  of hpa, keda, firm, static, gptcache on composite J (p < 0.01).
- **MX-H2 (secondary):** jcac remains the cost winner against each
  baseline (paired total_cost_usd, p < 0.01).
- **MX-H3 (violation non-inferiority):** PASS per baseline b ∈ {hpa,
  keda} iff the one-sided 99% upper confidence bound of the paired
  mean_violation diff (jcac − b) stays within (v1 paired diff + 0.02),
  v1 reference recomputed live from `raw_sim.duckdb` (at registration:
  hpa −0.0045, keda +0.0010).
- **Declared mechanism expectations** (directional, not hypotheses): the
  cache's violation benefit shrinks toward its work-absorption channel
  only (hits still unload the pool; they no longer beautify the tail);
  jcac should buy **less** cache than in v1 — the joint controller
  abandoning a devalued knob is adaptation, exactly as in the HK rerun;
  the cache-centric baseline (gptcache) is pressured most on J.

## Outcome handling and stopping rule

As PREREG_HK_ADOPTION: results to `RESULTS_MIXTURE_P95.md` as measured
(including any failure, stated verbatim); one execution of
`raw_sim_mixp95.duckdb`, valid only at 1,800/1,800; crash-resume allowed;
no widening.

## Exploratory arm (declared now, run only after all three Wave 5 primaries)

`matrix_structreal` = measured latency model **and** mixture percentile
**and** tier-scaled work units together (the "structurally realistic"
world), same 1,800-cell mirror. Exploratory: its reading informs
future-work text, not the headline. If it runs, it runs once under this
paragraph's declaration; its YAML must copy all three model_form blocks
verbatim.
