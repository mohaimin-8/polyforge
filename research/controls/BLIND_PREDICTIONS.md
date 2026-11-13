# Blind predictions of the coordination gain (WP22 item 4; exploratory)

Predictions made 2026-11-13T16:37:13 from the joint planner alone and committed before the per-knob runs (git history). 40 fresh regimes (seed 20261114), panel 4, 60 steps. Rule fitted on the search's random half: G = -0.0007 +0.454 x loss +0.0012 x index.

- Spearman(predicted, observed G) = +0.39, one-sided p = 0.0063: H2-blind holds at 0.05.
- Mean absolute error 0.0114 against 0.0135 for the null that predicts the random half's median gain everywhere.
- Spearman with G of the one-step loss alone +0.44, of the index alone +0.29.
- Observed G: mean +0.0045, median +0.0018, max +0.0868.
