# Notes on the protocol draft (not part of the registered text)

Written 2026-11-20 with the draft of `PROTOCOL.md`. The draft describes what
the built code does (`design.json`, `wp14_campaign.py`, `wp14_score.py`).
Where that differs from plan v2's Part D, or where the pilots point one way,
it is listed here for the author to decide before freezing. This file is not
hashed into the draw; keep it or delete it before commit C.

## Choices the draft makes that are the author's

| # | Plan v2, Part D | The draft (= the built scorer) | To follow Part D instead |
|---|---|---|---|
| 1 | primary metrics: cost per 1,000 requests at matched attainment, attainment at matched cost, unserved share | J, the composite objective; cost and violation differences reported, not tested | change the scorer before C |
| 2 | outcome ~ arm x regime + (1 \| workload); Holm across H2 to H5 and H7 | paired tests per workload (H3, H4) and Spearman over regimes (H2); Holm over H2 to H4, since H5 and H7 are not in this campaign | change the scorer before C |
| 3 | H3's margin: 2% of cost at matched attainment | 2% of mean J(joint) | follows from 1 |
| 4 | H4: one-sided, smallest effect from WP4 | one-sided against zero, no smallest effect in the decision | add a margin to the test (a scorer change), or state a smallest effect for interpretation only |
| 5 | H8 (WP17) among the registered hypotheses | outside this protocol | add WP17's live design; its main runs need 433 laptop-hours at power 0.9 (324 at 0.8), sized from the pilot (`testbed/hvpa/main_runs.py sizes`) |
| 6 | registered-report route (WP21) | not assumed | push C only after in-principle acceptance; this protocol is then the Stage 1 design |

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

## Scorer change made with the draft (2026-11-20)

H1 used to read as holding when no decision had been checked, and a dose
part in which every run failed stopped the scorer. It now holds only when at
least one decision is checked and none violates it
(`wp14_score.score_h1`, test `test_h1_holds_only_when_some_decision_was_checked`).

## Freezing and running

1. Edit `PROTOCOL.md`, and replace its Status line, for example: "Frozen in
   the commit that adds this line; its push is T0; registered on OSF before
   the round named in section 7."
2. In `design.json`, change any value, then set `"draft": false`.
3. Both must pass: `python research/registration/wp14_campaign.py --check`
   and `python research/registration/confirm.py shards`.
4. Commit (this is C) and push. Note the push time: T0.
5. `python research/registration/seeds.py round --t0 <T0 in UTC>` gives the
   round's number.
6. Register on OSF before that round is due, with the file, D, C's hash, T0
   and the round's number. Take D from the committed bytes, not the working
   copy (an editor on Windows may have saved it with CRLF line ends):
   `git show HEAD:research/registration/PROTOCOL.md | sha256sum` in Git Bash.
   Check that the registration's archived copy has the same SHA-256.
7. When the round is due: GitHub, Actions, `confirmatory`, Run workflow on
   C, with the round's number. It takes about 15 minutes.
8. The results: the run's `confirmatory` artifact (every shard's
   `rows.csv` and `population.json`, then `score.json` and `SCORE.md`) and
   `SHA256SUMS` in its summary. Then rebuild the deposit with them:
   `python scripts/bundle_programme2.py --audit ../PolyForge_Audit_2026-11-11_scripts --confirmatory <the artifact's folder>`.
