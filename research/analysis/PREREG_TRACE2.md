# Pre-registration — powered replay on real BurstGPT demand (second, independent sample)

Dated 2026-07-12 (session 15). Committed **before** any run. This is the new
pre-registration that PREREG_TRACE §4 explicitly permits ("a higher-powered replay
would be a new pre-registration in a new file") — it does not amend, rerun, or
reinterpret the first replay; RESULTS_TRACE.md stands as measured.

## §1 Why a second sample, and why this size

The first replay measured J diffs of −0.245…−0.280 (d_z ≈ −0.43) for `jcac` vs
every baseline — direction identical to the v1 headline in all comparisons — at
n = 16 windows, which yields only ~35% power against the pre-registered p < 0.01
bar. That is a sample-size failure, not an effect failure, and the honest remedy
is a second, larger, *independently drawn* sample, sized in advance:

**Power analysis (frozen):** at the observed d_z = 0.43, n = 96 windows gives
noncentrality 0.43·√96 ≈ 4.2 against t₀.₀₀₅(95) ≈ 2.63 → power ≈ 94%. n = 96 it is.

## §2 Protocol (frozen; §2 of PREREG_TRACE except where stated)

Identical demand construction: real per-tenant 600 s rates from
`burstgpt_real.csv.gz`, scale k from the same formula (mean per-tenant work =
100 wu/s), piecewise-constant to 10 s intervals, Poisson arrival jitter drawn
once per window and shared by every system. Differences, all fixed here:

- **96 windows of 6 h — 48 per contiguous segment**, window *i* at
  `seg_start + i · (seg_len − 6 h)/47`, i = 0..47 (inter-window spacing ≥ 56 h:
  non-overlapping, deterministic).
- **Jitter seeds 2000 + window index** — a fresh, independent noise draw; no
  window reuses a first-replay seed.
- **Systems: `jcac`, `jcac_v2`, `hpa`, `keda`, `firm`** (480 runs).
  `jcac_seasonal` is dropped: ET2 answered it (≈ tie under weak periodicity);
  carrying it adds 96 runs and no information.
- Cluster, tenants, budgets, engine semantics: unchanged.

## §3 Hypothesis (confirmatory)

**HT2:** identical to HT — paired by window, `jcac` shows composite J
significantly below **each** of HPA, KEDA, FIRM (paired t, p < 0.01). Cost and
violation diffs reported alongside; a J win accompanied by a violation
regression at p < 0.01 and |d_z| ≥ 0.5 vs the same baseline is
**PASS-with-disclosure**, stated prominently.

**Declared secondary (estimates, not gates):** per-segment stratified J/cost
diffs (the first replay showed the effect concentrates in the high-traffic
segment); `jcac_v2` vs `jcac` (ET1 replication at higher n).

## §4 Stopping rule

One run of the 480-cell set (crash retries only), one analysis pass, either
verdict published in `RESULTS_TRACE2.md`. No constant in §2 changes after the
run starts. There is no third replay sample inside this thesis: if HT2 fails,
the real-demand cost restatement is reported forever as "direction consistent,
significance not reached," alongside both samples.
