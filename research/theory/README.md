# research/theory — programme 2, work packages WP1, WP3, WP4 and WP18

Programme 1 ends at tag `programme-1-final`; this directory starts programme 2
(plan: `Thesis Project/PolyForge_Q1_Research_Plan_v2_detailed_2026-11-12.md`,
outside the repository). Everything here is **exploratory pilot work** on
seeds programme 1 already used; nothing here is a registered result.

| file | what it is |
|---|---|
| `INDEX_THEORY.md` | definitions and statements: the interaction index, Theorems 1 and 2, tightness (T3), sampling (T5), knobs as players of a potential game (T6), and the run-level question T4 (the plan's conjecture fails; drafts T4a and T4' hold on 100,000 checks); proofs not yet read by a second person |
| `interaction_index.py` | the statements as code, on any finite box of knob values |
| `controller_index.py` | the index of every decision of the simulator's joint planner (`IndexedController`); it changes no decision |
| `clairvoyant.py` | reference points for a run: lower bounds LB0, LB1, LB2 and an achievable plan UB for the clairvoyant optimum, and the best static plan |
| `index_pilot.py` → `INDEX_PILOT.md` | the index and Theorem 1 on every decision of WIDTH, JOINTNESS and LEVEL2 (replication 0) |
| `headroom_pilot.py` → `HEADROOM_PILOT.md` | the reference points for LEVEL2's 300 worlds, with every registered arm between them |
| `dose_response_pilot.py` → `DOSE_RESPONSE_PILOT.md` | WP4: the coordination gain and the index over 5 step widths x 5 couplings (25 regimes, 1,000 runs); uses `research/control2/width.py` |
| `property_cases.py`, `property_check.py` → `PROPERTY_CHECKS.md` | WP3 property checks: Theorems 1 and 2 on 100,000 random boxes against an independent numpy oracle, T3 on 100,000 random (rho, eta), T5's coverage computed exactly; seeded numpy, not Hypothesis (reasons in the report) |
| `t4_check.py` | WP18 T4: random multi-step instances under the plan's assumptions (a)-(c), per-knob and joint greedy runs, the conjectured run-level bound (fails: counterexamples), the two-step T4a and the within-reach T4'; results also in `PROPERTY_CHECKS.md` |
| `test_*.py` | property tests of the statements, tests against the planner's own searches, and the reference points against brute force and the engine's own scoring; `test_property_checks.py` and `test_t4_t6.py` run seeded samples of the 100,000-case checks, T6's game facts and T4's counterexample in about 15 seconds |

Run, from this directory:

```bash
python -m pytest -q          # about 2 minutes
python index_pilot.py        # about 20 minutes on 6 cores
python headroom_pilot.py     # about 10 minutes on 6 cores
python dose_response_pilot.py  # about 55 minutes on 6 cores
python property_check.py boxes --start 0 --count 12500   # 1 of 8 chunks (start 0, 12500, ..., 87500), about 100 s each
python property_check.py t3 && python property_check.py t5-tables
python t4_check.py target --start 0 --draws 25000        # 1 of 4 chunks, about 2 minutes each; then within, minimal, drift
python property_check.py report                         # writes PROPERTY_CHECKS.md from data/*.json
```
