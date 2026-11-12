# research/theory — programme 2, work packages WP1, WP3 and WP4

Programme 1 ends at tag `programme-1-final`; this directory starts programme 2
(plan: `Thesis Project/PolyForge_Q1_Research_Plan_v2_detailed_2026-11-12.md`,
outside the repository). Everything here is **exploratory pilot work** on
seeds programme 1 already used; nothing here is a registered result.

| file | what it is |
|---|---|
| `INDEX_THEORY.md` | definitions and statements: the interaction index, Theorems 1 and 2, tightness (T3), sampling (T5); proofs not yet read by a second person |
| `interaction_index.py` | the statements as code, on any finite box of knob values |
| `controller_index.py` | the index of every decision of the simulator's joint planner (`IndexedController`); it changes no decision |
| `clairvoyant.py` | reference points for a run: lower bounds LB0, LB1, LB2 and an achievable plan UB for the clairvoyant optimum, and the best static plan |
| `index_pilot.py` → `INDEX_PILOT.md` | the index and Theorem 1 on every decision of WIDTH, JOINTNESS and LEVEL2 (replication 0) |
| `headroom_pilot.py` → `HEADROOM_PILOT.md` | the reference points for LEVEL2's 300 worlds, with every registered arm between them |
| `dose_response_pilot.py` → `DOSE_RESPONSE_PILOT.md` | WP4: the coordination gain and the index over 5 step widths x 5 couplings (25 regimes, 1,000 runs); uses `research/control2/width.py` |
| `test_*.py` | property tests of the statements, tests against the planner's own searches, and the reference points against brute force and the engine's own scoring |

Run, from this directory:

```bash
python -m pytest -q          # about 2 minutes
python index_pilot.py        # about 20 minutes on 6 cores
python headroom_pilot.py     # about 10 minutes on 6 cores
python dose_response_pilot.py  # about 55 minutes on 6 cores
```
