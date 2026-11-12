# WP6 fits: h(c) = hmax * c / (c + half) per class, policy and tau

Bytes per entry (Go go1.26.10, real SemanticCache.Store path, 1536-byte vector): overhead beyond key and completion bytes 2384 B (range over runs 2381-2385); a minimal entry (15-byte key, no completion) holds 2332 B.

Fitted on the capacities whose cache was full when the warm half began, in three units: MiB of resident heap (plant 2's unit), MiB of completion bytes (the live knob's accounting) and entries. Intervals: 95% bootstrap over tenants.

| class | policy | tau | hmax [95%] | half MiB [95%] | half knob MiB | half entries |
|---|---|---|---|---|---|---|
| all | costaware | 0.75 | 0.099 [0.067, 0.136] | 0.097 [0.050, 0.170] | 0.033 | 25 |
| all | costaware | 0.80 | 0.062 [0.043, 0.171] | 0.090 [0.043, 0.522] | 0.030 | 23 |
| all | costaware | 0.85 | 0.040 [0.020, 0.064] | 0.100 [0.065, 0.230] | 0.034 | 25 |
| all | costaware | 0.90 | 0.012 [0.006, 1.000] | 0.227 [0.097, 89.590] | 0.076 | 58 |
| all | costaware | 0.95 | not fitted | | | |
| all | lru | 0.75 | 0.153 [0.116, 0.211] | 0.130 [0.083, 0.276] | 0.028 | 40 |
| all | lru | 0.80 | 0.151 [0.113, 0.214] | 0.192 [0.130, 0.404] | 0.041 | 59 |
| all | lru | 0.85 | 0.366 [0.140, 1.000] | 0.989 [0.258, 5.061] | 0.188 | 314 |
| all | lru | 0.90 | 0.270 [0.075, 1.000] | 1.214 [0.200, 7.994] | 0.221 | 392 |
| all | lru | 0.95 | 0.273 [0.061, 1.000] | 1.664 [0.213, 11.482] | 0.282 | 551 |
| conversation | costaware | 0.75 | 0.350 [0.064, 1.000] | 1.954 [0.258, 7.721] | 0.849 | 448 |
| conversation | costaware | 0.80 | 0.836 [0.027, 1.000] | 9.145 [0.144, 16.990] | 3.735 | 1738 |
| conversation | costaware | 0.85 | 1.000 [0.023, 1.000] | 29.504 [0.495, 84.497] | 10.058 | 7408 |
| conversation | costaware | 0.90 | 1.000 [0.961, 1.000] | 148.532 [71.646, 290.969] | 50.704 | 37260 |
| conversation | costaware | 0.95 | not fitted | | | |
| conversation | lru | 0.75 | 0.205 [0.122, 0.369] | 0.333 [0.188, 0.655] | 0.072 | 104 |
| conversation | lru | 0.80 | 0.182 [0.110, 0.418] | 0.354 [0.194, 1.002] | 0.077 | 110 |
| conversation | lru | 0.85 | 1.000 [0.206, 1.000] | 4.220 [0.726, 5.793] | 0.916 | 1283 |
| conversation | lru | 0.90 | 1.000 [0.123, 1.000] | 5.390 [0.646, 8.288] | 1.172 | 1639 |
| conversation | lru | 0.95 | 1.000 [0.226, 1.000] | 6.576 [1.431, 10.476] | 1.426 | 2002 |
| english | costaware | 0.75 | 0.069 [0.063, 0.083] | 0.029 [0.009, 0.117] | 0.010 | 7 |
| english | costaware | 0.80 | 0.046 [0.042, 0.113] | 0.020 [0.000, 0.370] | 0.007 | 5 |
| english | costaware | 0.85 | 0.030 [0.023, 0.053] | 0.009 [0.000, 0.309] | 0.003 | 2 |
| english | costaware | 0.90 | 0.017 [0.012, 1.000] | 0.120 [0.054, 21.233] | 0.041 | 30 |
| english | costaware | 0.95 | not fitted | | | |
| english | lru | 0.75 | 0.162 [0.138, 0.214] | 0.142 [0.091, 0.306] | 0.032 | 42 |
| english | lru | 0.80 | 0.130 [0.108, 0.185] | 0.177 [0.105, 0.392] | 0.040 | 52 |
| english | lru | 0.85 | 0.096 [0.056, 0.189] | 0.190 [0.089, 0.519] | 0.043 | 56 |
| english | lru | 0.90 | 0.070 [0.034, 1.000] | 0.321 [0.089, 8.825] | 0.073 | 94 |
| english | lru | 0.95 | 0.032 [0.012, 1.000] | 0.264 [0.036, 30.776] | 0.060 | 78 |
| other_language | costaware | 0.75 | 0.282 [n/a, n/a] | 0.125 [n/a, n/a] | 0.053 | 30 |
| other_language | costaware | 0.80 | 0.238 [n/a, n/a] | 0.238 [n/a, n/a] | 0.095 | 59 |
| other_language | costaware | 0.85 | 0.762 [n/a, n/a] | 1.628 [n/a, n/a] | 0.732 | 412 |
| other_language | costaware | 0.90 | 0.760 [n/a, n/a] | 1.625 [n/a, n/a] | 0.730 | 412 |
| other_language | costaware | 0.95 | 0.763 [n/a, n/a] | 1.631 [n/a, n/a] | 0.732 | 412 |
| other_language | lru | 0.75 | 0.313 [n/a, n/a] | 0.056 [n/a, n/a] | 0.011 | 18 |
| other_language | lru | 0.80 | 0.235 [n/a, n/a] | 0.042 [n/a, n/a] | 0.008 | 14 |
| other_language | lru | 0.85 | 0.194 [n/a, n/a] | 0.038 [n/a, n/a] | 0.007 | 12 |
| other_language | lru | 0.90 | 0.195 [n/a, n/a] | 0.040 [n/a, n/a] | 0.008 | 13 |
| other_language | lru | 0.95 | 0.186 [n/a, n/a] | 0.039 [n/a, n/a] | 0.008 | 13 |

Plant 2 default (all, costaware, tau 0.85): {"cache_hit_max": 0.039641578454554814, "cache_hit_max_ci95": [0.02021479901870609, 0.06392109222167393], "cache_half_mb": 0.09966867623460547, "cache_half_mb_ci95": [0.06499086095873104, 0.23045160555192476], "cache_half_mb_knob_unit": 0.03354194300719001}

