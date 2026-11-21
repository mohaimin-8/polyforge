# Notes on the H8 protocol draft (not part of the registered text)

Written 2026-11-21 with the draft of `PROTOCOL_H8.md`, at the author's request
("draft H8's protocol, with the recommendations marked"). The draft describes
what the built code does (`testbed/hvpa/main_runs.py`, `main_score.py`,
`main_design.json`). Where a choice is the author's, or the plan and the
pilot point one way, it is listed here with a recommendation. This file is
not hashed into the draw.

**2026-11-22 (local time), the author's decision:** every recommendation below accepted
("Apply + freeze tonight"). Rows 3 and 4 are in `testbed/hvpa/main_runs.py` (`main_order`, the sizing
after Holm), with a check that the draw was made for `PROTOCOL_H8.md`; the protocol says so and was
frozen as C8 (tag `wp17-c`).

## Choices that are the author's

| # | the draft (= the built code) | other options | recommended, and why |
|---|---|---|---|
| 1 | Holm over all six contrasts (3 regimes x A3 and A1), alpha 0.05 | Holm over H8b's three A1 contrasts only, the A3 contrasts reported with intervals and not tested; or no correction | keep the six: joint against per-knob is the coordination claim the index is about, so it is tested, and testing it uncorrected beside H8b would inflate the family's error |
| 2 | H8 holds when H8a holds and H8b is supported in every regime; each part reported on its own | H8b in at least one regime; or no overall verdict | keep: plan v2 states H8 as one sentence joined by "and" |
| 3 | schedule k runs in the order `pilot.order(k)`, k mod 6, and has the shape k mod 6, so each shape always runs in one order (6 of the 36 shape-order pairs) | rotate: order (k + floor(k / 6)) mod 6 | rotate. Checked at 66, 36, 42, 72 and 48 schedules: every arm still takes every place equally often and each block of six schedules still holds all six orders, but all 36 shape-order pairs occur, so a position effect that differs by shape no longer hides inside the shape's own effect. A one-line change in `main_runs.make_plan` before C8; the pilot is untouched. |
| 4 | sizes by WP12's formula per contrast, 90 % power: 66, 36 and 42 schedules, 433 laptop-hours | size for power 0.9 after row 1's Holm: 72, 42 and 48 schedules, 488 h (`main_runs.py sizes` simulates both) | size after Holm. At 66/36/42 the A3 contrasts keep 0.87 to 0.90 after the Holm the protocol registers, below the plan's 0.9; 72/42/48 keeps every contrast at 0.92 or more for 55 hours more. `make_plan` would size with `holm_schedules`, which `sizes` already uses. |
| 5 | superiority for every contrast; the design's equivalence margin (2 % of the comparator's mean J) unused | an equivalence test for a contrast the pilot puts near zero | keep superiority: H8 predicts that joint control lowers J, and no contrast was planned as an equivalence |
| 6 | A2 (HPA on request rate through KEDA, with the VPA) not run, as in the pilot | add A2 as a third comparator | keep it out: H8 names the same-metric pair (A1), the pilot has no A2 runs to size it from, and it would add about a third more testbed time. Say so as a limit of the result. |
| 7 | registration on OSF after C8 is pushed | the registered-report route (WP21) | the author's, with the supervisor; if chosen, push C8 only after in-principle acceptance |

## What the pilot suggests

- **A3 contrasts:** the pilot's gains (+20 % to +60 %) are as large as the
  twin's predictions or larger, so these are likely supported.
- **A1 at w4 s0.2:** the pilot's gain is -10.2 % (the joint arm's J above
  the pair's), against the twin's +52 %. The size rests on the twin's
  prediction; at the pilot's effect no size gives power, so H8b, and with it
  H8 under row 2, is likely not supported there. Sizing on the pilot's
  observed effects instead would fit the registration to the pilot: not
  recommended.
- **H8a:** on the pilot's gains the three regimes rank w4 s0.2, w2 s0.2,
  w4 s0.05 by gain and w2 s0.2, w4 s0.2, w4 s0.05 by index, rho +0.5 (the
  criterion asks for +1 with three regimes). Over all nine simulated
  regimes the twin itself gave +0.18.

## Practical

- About 18 days (433 h) or 20 (488 h) of continuous testbed time on one
  host: on mains (a run on battery is invalid and repeated), the host kept
  awake, the run detached (`tools/detach.py`). WP14's soak history shows
  power-source changes stalling this host; check the AC adapter first.
- The testbed was stopped on 2026-11-21 (nothing deleted):
  `docker start polyforge-testbed-control-plane`, wait for the node to be
  Ready, and check WP17's components (`testbed/hvpa/install.py`) before any
  run.

## Freezing and running

1. Apply the accepted choices (rows 3 and 4 change `main_runs.make_plan`,
   with tests), replace the Status line of `PROTOCOL_H8.md`, and make every
   number in it match (sections 4 and 9).
2. In `testbed/hvpa/main_design.json`, set `"draft": false`.
3. Tests pass (`python -m pytest testbed -q`), and `python
   testbed/hvpa/main_runs.py sizes --pilot testbed/hvpa/pilot_runs` prints the
   sizes the protocol states.
4. Commit (C8), tag it (`git tag wp17-c`), push both.
5. `python research/registration/facts.py --tag wp17-c --protocol
   research/registration/PROTOCOL_H8.md --design
   testbed/hvpa/main_design.json` prints C8, D8, T0, the round and the hours
   left to register.
6. Register on OSF before the round is due.
7. When it is due: `seeds.py draw` (protocol section 7), then `main_runs.py
   plan` (section 8); commit the draw and the plan, push.
8. Restart the testbed, then `main_runs.py run`, detached.
9. Keep the records, commit them, score once (section 8).
