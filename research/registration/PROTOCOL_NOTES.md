# Notes on the protocol draft (not part of the registered text)

Written 2026-11-20 with the draft of `PROTOCOL.md`. The draft describes what
the built code does (`design.json`, `wp14_campaign.py`, `wp14_score.py`).
Where that differs from plan v2's Part D, or where the pilots point one way,
it is listed here for the author to decide before freezing, each with a
recommendation. This file is not hashed into the draw; keep it or delete it
before commit C.

## Choices the draft makes that are the author's

| # | Plan v2, Part D | The draft (= the built scorer) | Recommended, and why |
|---|---|---|---|
| 1 | primary metrics: cost per 1,000 requests at matched attainment, attainment at matched cost, unserved share | J, the composite objective; cost and violation differences reported, not tested | keep J: every pilot, WP9's selection and the sample size are on J, and a new matched-attainment scorer written now would be untested code inside a frozen campaign |
| 2 | outcome ~ arm x regime + (1 \| workload); Holm across H2 to H5 and H7 | paired tests per workload (H3, H4) and Spearman over regimes (H2); Holm over H2 to H4, since H5 and H7 are not in this campaign | keep: with two arms and one run each per workload, the paired test is that model's contrast |
| 3 | H3's margin: 2% of cost at matched attainment | 2% of mean J(joint) | keep (follows from 1; 120 workloads exceed the 76 the margin needs) |
| 4 | H4: one-sided, smallest effect from WP4 | one-sided against zero, no smallest effect in the decision | keep the test, and add one sentence to H4 naming 2% of mean J(joint) as the smallest effect of interest for interpretation (the effect the sample size was planned for); text only, no scorer change |
| 5 | H8 (WP17) among the registered hypotheses | outside this protocol | keep it outside: its main runs need 433 laptop-hours at power 0.9 (324 at 0.8, `testbed/hvpa/main_runs.py sizes`), and their scorer is drafted, not frozen (`testbed/hvpa/main_score.py` with `main_design.json`, marked draft). `main_runs.py plan --draw` seeds their demand from a registered draw (identities `wp17\|w<width>\|s<sigma>\|k<k>`, distinct from WP14's), so they can take this protocol's draw if H8 is added here, or a second protocol's own draw (`seeds.py draw --protocol <that file>`) when they can be run. If any WP17 contrast is planned as an equivalence test, its margin is 2 % of the comparator's mean J, as the pilot sized it (`pilot.sample_sizes`) and `main_score.py` scores it, where WP14's H3 takes 2 % of the joint arm's mean J: H8's registration should name one |
| 6 | registered-report route (WP21) | not assumed | the author's, with the supervisor (WP21 step 1); if chosen, push C only after in-principle acceptance |
| 7 | H2's predictor: the index, rho >= 0.8 | the same (`design.json`: `predictor` `index`) | keep: the index is the claim under test; switching to the pilot's best predictor (the realised loss, rho 0.79) would fit the registration to the pilot and is still below 0.8 |

## What the pilots suggest

- **H2:** in the WP4 pilot the registered predictor (the worst-case index)
  reached rho +0.74 over 25 regimes, below the 0.8 the plan registers; the
  realised one-step loss reached +0.79. `design.json`'s `predictor` can be
  `index`, `quantile_index`, `loss` or `costly`. The predictor and the
  threshold can change only before C.
- **H3:** WP9's evaluation half found no difference between joint and
  per-knob search in plant A, so equivalence is likely.
- **H4:** WP9's evaluation half found none in plant B either (-0.0000
  [-0.0006, +0.0006]), so H4 is likely not supported. The account calls
  either outcome a result (Part A3).

## Cross-check of the draft (2026-11-20)

Every number and name in `PROTOCOL.md` was checked against its source:
the run counts (800 dose runs; 600 + 2c and 960 - c comparison runs for c
drawn `conversation` workloads, 2,420 at c = 60 and 2,431 at c = 71) and
the workload names (`pop1000` to `pop1119`) from `wp14_campaign.plan`; the seed identities, the modulus and
the relays from `wp14_campaign.py` and `seeds.py`; alpha, the margin, the
slope range, the rho threshold, the permutations, the kappa range and
strata and the shard count from `design.json` and `campaign.json`; the
tests from `wp14_score.py`; the pilot numbers from `DOSE_RESPONSE_PILOT.md`,
the WP9 reports and `SAMPLE_SIZE.md`; H5 and H6 from `testbed/wp12/PILOT.md`
and `testbed/validation/VALIDATION.md` / `VALIDATION_V2.md`; the cache radii
from `research/control2/width.py`; the CI image, Python and dependency file
from `.github/workflows/confirmatory.yml`. Three statements were corrected
(where the rehearsals ran; how an incomputable test is reported; the run
count, which depends on the draw: a full-scale check with another made-up
seed built 2,431 jobs, not the 2,420 of the rehearsal's draw).

## Scorer change made with the draft (2026-11-20)

H1 used to read as holding when no decision had been checked, and a dose
part in which every run failed stopped the scorer. It now holds only when at
least one decision is checked and none violates it
(`wp14_score.score_h1`, test `test_h1_holds_only_when_some_decision_was_checked`;
commit 4920f02, CI green, run 44688664965).

## Freezing and running

1. Edit `PROTOCOL.md`, and replace its Status line, for example: "Frozen in
   the commit that adds this line; its push is T0; registered on OSF before
   the round named in section 7."
2. In `design.json`, change any value, then set `"draft": false`.
3. Both must pass: `python research/registration/wp14_campaign.py --check`
   and `python research/registration/confirm.py shards`.
4. Commit (this is C), tag it (`git tag wp14-c`) and push both
   (`git push origin research-v2 wp14-c`). T0 is GitHub's record of the
   push, not a time noted by hand (rounds come every 3 s, so seconds
   matter): `gh run list --commit <C's full hash> --json createdAt`, the
   earliest CI run of C. The draft defines T0 that way (section 7).
5. `python research/registration/seeds.py round --t0 <T0 in UTC>` gives the
   round's number.
6. Register on OSF before that round is due, with the file, D, C's hash, T0
   and the round's number. Take D from the committed bytes, not the working
   copy (an editor on Windows may have saved it with CRLF line ends):
   `git show HEAD:research/registration/PROTOCOL.md | sha256sum` in Git Bash.
   Check that the registration's archived copy has the same SHA-256.
   Steps 4 to 6 in one command, once the push has its first run:
   `python research/registration/facts.py --tag wp14-c` prints C, D (from
   the committed bytes), T0 (the first run's creation time), the round and
   the hours left to register; it refuses a tag whose design.json is still
   draft. Rehearsed on 2026-11-20 in a scratch clone against the live chain
   and a live draw (`confirm.py draw` fetched the round it named from two
   relays; an early round was refused, a future one was "not yet").
7. When the round is due, push a tag named after it at C:
   `git tag wp14-round-<N> wp14-c` and `git push origin wp14-round-<N>`;
   the `confirmatory` workflow starts on that push (Actions, confirmatory).
   GitHub offers "Run workflow" only for a workflow on the default branch,
   and on 2026-11-20 it did not list `confirmatory` at all (dispatch-only,
   never run, not on main); once `confirmatory.yml` is on main, "Run
   workflow" from the tag `wp14-c` with the round does the same. Pushed
   before the round is due, the draw stops with "not yet": re-run the
   failed jobs once it is due. Never a
   later commit (a deviation logged after C moves the branch): `confirm.py`
   refuses a round due less than 24 h after the commit it runs on. It takes
   about 15 minutes (the full campaign took 10.4 worker-hours in the
   laptop's completion check: about 10 minutes over 16 runners of 4 CPUs,
   plus setup).
8. The results: the run's `confirmatory` artifact (every shard's
   `rows.csv` and `population.json`, then `score.json` and `SCORE.md`) and
   `SHA256SUMS` in its summary. Then rebuild the deposit with them:
   `python scripts/bundle_programme2.py --audit ../PolyForge_Audit_2026-11-11_scripts --confirmatory <the artifact's folder>`.
