# research/external/routebalance — programme 2, work package WP15

The interaction index on RouteBalance's fused routing and load-balancing decision (arXiv 2606.17949v2).
Exploratory. What was simplified and what it found: `REPORT.md`; the generated numbers: `RESULTS.md`.

| file | what it is |
|---|---|
| `rb_model.py` | one decision as a box and an objective (phi = 1 - S of their Eq. 2-3), anchor = RB-decoupled |
| `fleet.py` | decision states from their released inputs (trace, rounds, telemetry, a simulated plant) |
| `analyse.py` | calibration, runs per preset and rate, the index, Theorem 1 and index-probe checks; `report` writes `RESULTS.md` |
| `our_scale.py` | our pilots' index and one-step loss as shares of the objective, for the common scale |
| `results.py` | the report generator |

Their repository goes in `upstream/` (git-ignored) and is read as data, never run:

```bash
git clone --depth 1 --branch submission https://github.com/AKafakA/route-balance upstream
python -I analyse.py report      # rebuilds RESULTS.md from data/*.json
```
