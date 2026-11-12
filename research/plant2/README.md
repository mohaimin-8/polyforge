# research/plant2 — programme 2, work package WP2

Plant 2: a simulator plant built only from measured or swept constants
(plan: `Thesis Project/PolyForge_Q1_Research_Plan_v2_detailed_2026-11-12.md`,
outside the repository). Exploratory until a registration says otherwise.

| file | what it is |
|---|---|
| `queueing.py` | latency against load, two forms: the empirical (1 - rho)^(-a) and the Ramani-Tantawi chain (arXiv:2609.20957, implemented from its TeX source) |
| `fits.py` → `FITS.md`, `data/fits.json` | both forms fitted to `research/calibration/` data; split-half held-out errors; bootstrap intervals |
| `measure_load_curve.py` | re-measures the CPU server's load curve keeping every request (ten levels, two passes); writes `data/load_curve_*` |
| `test_*.py` | the chain against closed forms (M/M/1, processor sharing, the paper's limiting cases) |

```bash
python -m pytest -q
python fits.py
python measure_load_curve.py    # about 46 minutes; needs the CPU to itself
```
