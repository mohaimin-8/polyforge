# research/plant2 — programme 2, work package WP2

Plant 2: a simulator plant built only from measured, fitted, priced or swept
constants (plan: `Thesis Project/PolyForge_Q1_Research_Plan_v2_detailed_2026-11-12.md`,
outside the repository). Exploratory until a registration says otherwise.

| file | what it is |
|---|---|
| `queueing.py` | latency against load, two forms: the empirical (1 - rho)^(-a) and the Ramani-Tantawi chain (arXiv:2609.20957, implemented from its TeX source) |
| `fits.py` → `FITS.md`, `data/fits.json` | both forms fitted to this project's measurements: the committed six-level CPU curve, the re-measured ten-level curve (bootstrap over requests), and the GPU tiers' static batches; split-half held-out errors |
| `measure_load_curve.py` → `data/load_curve_*` | re-measures the CPU server's load curve keeping every request (ten levels, two passes, 2,186 requests on 2026-11-12) |
| `plant.py` → `CONSTANTS.md` | the two modes as simulator overrides (`mode_a`, `mode_b`) and the constants table: value, status, source, WP5 range |
| `test_queueing.py` | the chain against closed forms (M/M/1, processor sharing, the paper's limiting cases) |
| `test_plant.py` | the plant-2 mechanics in `research/jcac_sim/model.py` (admission gate, attainment, replica price, CRUD scale); with them off, `evaluate_step` equals tag `programme-1-final` bit for bit on random states |

The mechanics live in `model.py` behind default-off globals (`set_economy(replica_cost_usd_hr=...)`,
`set_model_form(wu_crud_scale=..., admission_gate=..., attainment_slo=...)`) and reach runs through the
harness keys of the same names, so the planner and the plant always share one model.

What the measurements say so far (details in `FITS.md`):
- the power form and the Ramani-Tantawi chain fit the CPU curve equally well (held-out 11.6% vs 11.9%); the simulator keeps the power form;
- the congestion exponent differs between two runs on the same laptop by more than either run's interval (0.86, 0.66), so it is a swept constant;
- p95/mean is about 1.8 and flat in load (the published 1.4 is too low; the July run's rising tail did not replicate);
- the paper's iteration law fits the GPU tiers' static batches within 3-4% and predicts a separate single-stream run within 5-8%.

Open: mode A's per-request tier prices (RouterBench, WP7); answer quality per tier (WP7), without
which the tier knob has no trade-off in either mode (the bigger tier is both slower and dearer).

```bash
python -m pytest -q
python fits.py
python plant.py
python measure_load_curve.py    # about 46 minutes; needs the CPU to itself
```
