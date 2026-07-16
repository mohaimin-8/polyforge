# Pre-registration: over-the-wire cache side-channel attack (Wave 3, closes DEFENSE_QA #19 security half)

Registered 2026-07-16 (session 20). Committed and pushed **before** the run;
push event is the timestamp anchor; to be mirrored to OSF prospectively.
**Execution is deferred to a live Codespace session** (kind cluster, no
Docker locally — see [[polyforge-local-env-constraints]]); this file freezes
the protocol so the live run is confirmatory, not improvised.

## Question

The AUC-0.88 membership attack (`cache_side_channel.py`) runs in the sim's
timing model, with attacker-side noise a fixed 25 ms Gaussian
(`NETWORK_JITTER_MS`). DEFENSE_QA #19 concedes it is model-level, an
upper-bound reading, and names the closure: demonstrate the same attack
**over the real network** against the live gateway, where genuine RTT
jitter — the defender's friend — sets the true attacker signal. And confirm
the defense conclusion (per-tenant partitioning returns the attacker to
chance) holds on the wire, not just in the model.

## Substrate (frozen)

- The W39 kind cluster from `scripts/phase7_bootstrap.sh`, gateway reachable
  at the same in-cluster address the harness replay path uses
  (`/v1/tenants/{tenant}/...`, `cluster_backend.py`).
- Two cache postures, both already in the codebase, toggled by chart value
  (integration point 2, `eval/README.md`): SHARED
  (`NewSemanticCache`, no per-tenant policy) vs PER-TENANT
  (`policyFor(tenantID)`, the W28 default — `internal/ai/gateway/cache.go`).
- Two tenants: `victim` (holds a set of secret prompts), `attacker`
  (separate API key, no access to victim data).

## Procedure (frozen)

1. Warm-up: victim issues each of its N_SECRET = 50 secret prompts once so
   they are cache-resident under the shared posture.
2. Attack set: 50 positive probes = paraphrases of the secrets (cosine ≥ the
   deployed threshold 0.85, generated offline and committed as a fixture so
   the probe set is identical across postures and re-runnable); 50 negative
   probes = unrelated prompts of matched token length.
3. The attacker issues all 100 probes, R = 20 repetitions each in randomized
   order, recording wall-clock response time client-side (the real
   over-the-wire measurement — RTT jitter included, no injected noise).
4. Threshold classifier on median per-probe response time; AUC over the
   100 probes by membership label. Repeat under both cache postures.
5. Latency ground truth for calibration (not part of the AUC): the gateway's
   own hit/miss flag per response, exported so the measured timing gap can
   be reported beside the AUC.

Randomization/blinding: probe order shuffled per repetition with a committed
seed; the classifier sees only response times, never the hit flag.

## Hypotheses (frozen)

- **WA-H1 (primary, defense):** under the PER-TENANT posture the attacker's
  membership AUC is within [0.45, 0.55] (indistinguishable from chance 0.50
  by a bootstrap 95% CI that contains 0.50). This is the claim the thesis
  actually rests on.
- **WA-H2 (secondary, attack reality):** under the SHARED posture the AUC is
  > 0.50 with a bootstrap 95% CI excluding 0.50 — the channel is real on the
  wire. **No specific magnitude is pre-committed**: real RTT jitter is
  expected to pull the wire AUC *below* the sim's 0.88, and reporting
  whatever it is (even if much weaker) is the honest point. A SHARED AUC at
  chance would itself be a finding — it would mean real jitter alone closes
  the channel, strengthening not weakening the safety story — and is
  reported as measured.
- **WA-H3 (descriptive):** the measured hit/miss timing gap on the wire vs
  the sim's 800 ms/20 ms, to state how much of the sim's attacker advantage
  was modeling.

## Amendment (declared pre-run, session 22 — substrate + threat model)

The frozen substrate above assumed the kind cluster gateway with a semantic
(MiniLM) embedder at threshold 0.85. The deployable offline substrate differs,
and this amendment is committed and pushed **before** any wire number is
collected, so the pre-registration discipline holds:

1. **Substrate = the real `cmd/ai-gateway` process** run on the Codespace host
   (a real Go HTTP server with the production `SemanticCache`), backed by the
   **deployed local n-gram embedder** (`embed.NewLocal`, lexical, offline —
   there is no external embedding API available offline) and a stdlib mock LLM
   backend (`research/security/mock_llm.py`, fixed ~250 ms miss delay) so a
   cache *miss* costs real wall time. This is a real-HTTP, real-cache,
   real-timing measurement over the loopback interface — a rung above the
   simulator's timing *model*, below a WAN cluster. It carries genuine
   (small) loopback + server jitter, not injected noise; it does **not** carry
   WAN RTT jitter, so it is not the "over-the-wire on a live gateway" WAN
   demonstration and is not claimed as one.
2. **Threat = exact-prompt membership.** The positive probe is the *exact*
   secret prompt (the attacker tests "is this specific prompt cached?"), the
   canonical membership-inference form, rather than a semantic paraphrase.
   This is embedder-independent — it works at the deployed 0.95 threshold on
   the lexical embedder — and is the conservative standard threat; it is an
   *upper bound* on attacker power (a paraphrase attacker is strictly weaker),
   which only strengthens the WA-H1 defense reading (isolation must kill even
   the exact-prompt channel).
3. **Consequences for the hypotheses:** WA-H1 (per-tenant AUC ≈ chance) and
   WA-H2 (shared AUC > chance) are unchanged in meaning. WA-H2's magnitude was
   never pre-committed; on loopback with a deterministic hit/miss gap it is
   expected to be high (near the sim's upper bound), and that is reported as
   measured, explicitly as a loopback-substrate reading. The insecure shared
   posture is provided by the pre-registered, unit-tested `POLYFORGE_CACHE_SHARED`
   flag (off by default; `TestSharedCachePostureLeaksCrossTenant`).

## Outcome handling and stopping rule

Results to `RESULTS_WIRE_ATTACK.md` as measured, including a WA-H1 failure
(a live per-tenant leak would be a serious finding and would headline the
limitations, not be buried). One execution per posture on the provisioned
cluster; the probe fixture and seeds may not change after this push; no
second attacker model (membership inference remains the only threat class,
as disclosed).
