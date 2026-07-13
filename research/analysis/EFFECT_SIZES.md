# Effect sizes with bootstrap CIs — real-trace replay campaigns

Recomputed from the committed run tables (no new runs); see the docstring of `effect_sizes.py` for reading rules. d_z = mean paired diff / SD of paired diffs; 95% percentile bootstrap, B = 10,000, seed 42, windows resampled as the pairing unit. Negative diffs favor the treatment (lower J/cost/violation).

## first sample (n=16, underpowered — transparency only)

| treatment vs baseline | metric | mean diff [95% CI] | d_z [95% CI] |
|---|---|---|---|
| jcac vs hpa (confirmatory) | J | -0.246 [-0.550, -0.028] | -0.43 [-0.72, -0.28] |
| jcac vs hpa | cost $ | -73.329 [-163.314, -8.579] | -0.44 [-0.72, -0.28] |
| jcac vs hpa | violation | +0.074 [+0.009, +0.165] | +0.44 [+0.29, +0.73] |
| jcac vs keda (confirmatory) | J | -0.245 [-0.557, -0.027] | -0.43 [-0.72, -0.28] |
| jcac vs keda | cost $ | -73.069 [-162.400, -8.659] | -0.43 [-0.72, -0.28] |
| jcac vs keda | violation | +0.074 [+0.010, +0.164] | +0.44 [+0.29, +0.72] |
| jcac vs firm (confirmatory) | J | -0.280 [-0.595, -0.058] | -0.48 [-0.99, -0.35] |
| jcac vs firm | cost $ | -76.491 [-168.490, -13.016] | -0.46 [-0.82, -0.33] |
| jcac vs firm | violation | +0.068 [+0.007, +0.155] | +0.43 [+0.26, +0.70] |
| jcac_v2 vs hpa | J | -0.252 [-0.568, -0.030] | -0.43 [-0.73, -0.28] |
| jcac_v2 vs hpa | cost $ | -74.767 [-167.845, -9.245] | -0.44 [-0.72, -0.29] |
| jcac_v2 vs hpa | violation | +0.075 [+0.010, +0.171] | +0.44 [+0.28, +0.72] |
| jcac_v2 vs keda | J | -0.250 [-0.573, -0.029] | -0.43 [-0.73, -0.28] |
| jcac_v2 vs keda | cost $ | -74.507 [-166.436, -8.606] | -0.43 [-0.73, -0.28] |
| jcac_v2 vs keda | violation | +0.075 [+0.009, +0.170] | +0.44 [+0.28, +0.72] |
| jcac_v2 vs firm | J | -0.285 [-0.606, -0.060] | -0.49 [-0.98, -0.36] |
| jcac_v2 vs firm | cost $ | -77.928 [-169.314, -13.505] | -0.46 [-0.82, -0.33] |
| jcac_v2 vs firm | violation | +0.069 [+0.007, +0.159] | +0.42 [+0.26, +0.70] |

## powered sample (n=96, confirmatory — cite these)

| treatment vs baseline | metric | mean diff [95% CI] | d_z [95% CI] |
|---|---|---|---|
| jcac vs hpa (confirmatory) | J | -0.269 [-0.405, -0.159] | -0.43 [-0.62, -0.35] |
| jcac vs hpa | cost $ | -77.647 [-111.242, -49.244] | -0.50 [-0.63, -0.41] |
| jcac vs hpa | violation | +0.075 [+0.048, +0.104] | +0.54 [+0.44, +0.65] |
| jcac vs keda (confirmatory) | J | -0.266 [-0.401, -0.157] | -0.43 [-0.62, -0.35] |
| jcac vs keda | cost $ | -77.216 [-110.173, -49.294] | -0.50 [-0.62, -0.41] |
| jcac vs keda | violation | +0.075 [+0.048, +0.104] | +0.54 [+0.44, +0.64] |
| jcac vs firm (confirmatory) | J | -0.299 [-0.435, -0.188] | -0.47 [-0.71, -0.39] |
| jcac vs firm | cost $ | -79.805 [-112.015, -51.860] | -0.52 [-0.65, -0.43] |
| jcac vs firm | violation | +0.068 [+0.042, +0.095] | +0.51 [+0.41, +0.62] |
| jcac_v2 vs hpa | J | -0.280 [-0.414, -0.168] | -0.44 [-0.63, -0.37] |
| jcac_v2 vs hpa | cost $ | -78.696 [-112.432, -50.097] | -0.50 [-0.63, -0.41] |
| jcac_v2 vs hpa | violation | +0.073 [+0.047, +0.103] | +0.51 [+0.42, +0.62] |
| jcac_v2 vs keda | J | -0.277 [-0.415, -0.166] | -0.44 [-0.62, -0.36] |
| jcac_v2 vs keda | cost $ | -78.265 [-111.633, -49.504] | -0.50 [-0.63, -0.41] |
| jcac_v2 vs keda | violation | +0.073 [+0.046, +0.103] | +0.51 [+0.42, +0.62] |
| jcac_v2 vs firm | J | -0.310 [-0.450, -0.199] | -0.48 [-0.71, -0.40] |
| jcac_v2 vs firm | cost $ | -80.854 [-113.672, -52.618] | -0.52 [-0.65, -0.43] |
| jcac_v2 vs firm | violation | +0.066 [+0.040, +0.094] | +0.49 [+0.38, +0.59] |

Citation guidance: report the n=96 d_z with its CI; give the paired-t p-value once as the prereg gate outcome and do not headline p-values below ~1e-6 — at this many paired simulated runs they measure determinism, not effect.
