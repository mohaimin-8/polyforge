# Cover letter — Future Generation Computer Systems

To the Editor-in-Chief, *Future Generation Computer Systems*

Please consider the enclosed manuscript, "Per-layer optima do not compose:
joint replica, semantic-cache and model-tier control for multi-tenant AI
serving, under pre-registration", for publication as a regular article.

**Why FGCS.** The paper sits where FGCS's readership works: the control plane
of multi-tenant clusters that serve conventional web traffic and AI inference
side by side. It contributes a Kubernetes-native controller that decides
replicas, semantic-cache budget and model tier for every tenant together, a
cost model that accounts per-tenant spend in dollars against a budget, and an
evaluation methodology — forty-eight pre-registered protocols, tuned
baselines, paired effect sizes with bootstrap intervals, substrates never
mixed — that we believe is the first of its kind in a systems venue. FGCS's
long-form format lets the full evidence chain, including the failures, be
reported rather than compressed away.

**The claim and its evidence.** On the composite objective every baseline
was tuned on, the joint controller wins in every campaign that tested it —
a 1,800-run factorial, replays of the BurstGPT and Azure LLM 2024 traces, a
2026-style concurrency scaler, a 32-tenant slice and an offline-trained
learned controller over the same action space — and is non-dominated in 48
of 60 cells under any weighting. The cache reaches 29.6 % hits at cosine
0.85 on real LMSYS-Chat-1M prompts with measured precision and its ceiling;
a timing side channel in shared semantic caches (AUC 0.88) is closed by
per-tenant partitioning and confirmed at chance over the wire; and on a
rented GPU host with every knob live, a corrected joint controller beats
every single-knob ablation on cost at equal fairness with intervals that
survive a block bootstrap.

**The honest scope.** The comparative cost percentages we first obtained
were withdrawn under three pre-registered re-scorings against corrected
comparators, and the paper reports them only as history beside the
surviving feasibility result: a per-tenant budget can be kept only by a
controller that holds the model-tier knob. Two pre-registered attempts to
beat tuned reactive scalers on SLO attainment failed and are published as
failures. The published controller lost to its own ablation on the live
plane before the corrected one won, and the first damper for the corrected
controller's oscillation failed its test. Headline numbers are decision
quality under a stated, partially calibrated system model; the paper claims
no absolute dollar or latency transfer to a production fleet.

**Prior and overlapping work.** The material derives from the first author's
B.Sc. thesis at Rajshahi University of Engineering & Technology, which is
not published in a journal or conference. No part of this manuscript has
been published or is under consideration elsewhere. All raw data,
pre-registrations and records are archived at Zenodo
(doi:10.5281/zenodo.22801195) and the pre-registrations at OSF
(doi:10.17605/OSF.IO/DYZKV); the reproduction gate regenerates every record
and figure from the committed data with one command.

The authors declare no competing interests. We look forward to the
reviewers' scrutiny; the record was built to withstand it.

Sincerely,

Md. Mohaiminul Islam (corresponding author)
Farzana Akter
Department of Electronics & Telecommunication Engineering
Rajshahi University of Engineering & Technology, Rajshahi 6204, Bangladesh
