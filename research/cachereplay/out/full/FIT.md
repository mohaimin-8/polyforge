# WP6 fits: h(c) = hmax * c / (c + half) per class, policy and tau

Bytes per entry (Go go1.26.10, real SemanticCache.Store path, 1536-byte vector): overhead beyond key and completion bytes 2384 B (range over runs 2383-2386); a minimal entry (15-byte key, no completion) holds 2324 B.

Fitted on the capacities whose cache was full when the warm half began, in three units: MiB of resident heap (plant 2's unit), MiB of completion bytes (the live knob's accounting) and entries. Intervals: 95% bootstrap over tenants.

| class | policy | tau | hmax [95%] | half MiB [95%] | half knob MiB | half entries |
|---|---|---|---|---|---|---|
| all | costaware | 0.75 | 0.312 [0.310, 0.313] | 0.754 [0.734, 0.772] | 0.265 | 187 |
| all | costaware | 0.80 | 0.264 [0.263, 0.265] | 1.019 [0.992, 1.048] | 0.354 | 254 |
| all | costaware | 0.85 | 0.211 [0.210, 0.213] | 1.194 [1.148, 1.239] | 0.413 | 299 |
| all | costaware | 0.90 | 0.160 [0.158, 0.162] | 1.190 [1.108, 1.279] | 0.412 | 297 |
| all | costaware | 0.95 | 0.118 [0.117, 0.120] | 1.092 [0.997, 1.193] | 0.378 | 273 |
| all | lru | 0.75 | 0.329 [0.328, 0.331] | 0.883 [0.869, 0.896] | 0.201 | 261 |
| all | lru | 0.80 | 0.281 [0.280, 0.282] | 1.086 [1.073, 1.102] | 0.248 | 322 |
| all | lru | 0.85 | 0.230 [0.229, 0.231] | 1.217 [1.192, 1.240] | 0.277 | 361 |
| all | lru | 0.90 | 0.178 [0.177, 0.179] | 1.169 [1.145, 1.192] | 0.266 | 347 |
| all | lru | 0.95 | 0.133 [0.132, 0.134] | 0.949 [0.928, 0.969] | 0.215 | 282 |
| code | costaware | 0.75 | 0.447 [0.440, 0.454] | 0.654 [0.631, 0.668] | 0.213 | 155 |
| code | costaware | 0.80 | 0.410 [0.404, 0.417] | 0.761 [0.720, 0.801] | 0.252 | 179 |
| code | costaware | 0.85 | 0.389 [0.384, 0.398] | 1.393 [1.215, 1.610] | 0.460 | 330 |
| code | costaware | 0.90 | 0.378 [0.373, 0.387] | 4.114 [3.781, 4.535] | 1.342 | 979 |
| code | costaware | 0.95 | 0.387 [0.378, 0.393] | 18.305 [17.420, 19.209] | 6.009 | 4352 |
| code | lru | 0.75 | 0.461 [0.456, 0.469] | 0.692 [0.656, 0.732] | 0.162 | 183 |
| code | lru | 0.80 | 0.435 [0.425, 0.443] | 0.962 [0.928, 1.014] | 0.229 | 254 |
| code | lru | 0.85 | 0.425 [0.414, 0.433] | 1.995 [1.902, 2.056] | 0.485 | 525 |
| code | lru | 0.90 | 0.424 [0.416, 0.438] | 5.489 [5.207, 5.880] | 1.349 | 1441 |
| code | lru | 0.95 | 0.427 [0.422, 0.433] | 19.273 [18.658, 20.399] | 4.745 | 5054 |
| conversation | costaware | 0.75 | 0.325 [0.323, 0.326] | 0.793 [0.771, 0.817] | 0.283 | 198 |
| conversation | costaware | 0.80 | 0.275 [0.273, 0.277] | 1.093 [1.058, 1.130] | 0.383 | 275 |
| conversation | costaware | 0.85 | 0.219 [0.217, 0.221] | 1.189 [1.131, 1.247] | 0.416 | 300 |
| conversation | costaware | 0.90 | 0.166 [0.164, 0.168] | 0.993 [0.927, 1.066] | 0.348 | 250 |
| conversation | costaware | 0.95 | 0.128 [0.126, 0.129] | 0.795 [0.725, 0.873] | 0.279 | 200 |
| conversation | lru | 0.75 | 0.345 [0.343, 0.346] | 0.957 [0.938, 0.979] | 0.217 | 288 |
| conversation | lru | 0.80 | 0.292 [0.290, 0.293] | 1.105 [1.085, 1.126] | 0.250 | 333 |
| conversation | lru | 0.85 | 0.237 [0.235, 0.238] | 1.109 [1.087, 1.134] | 0.250 | 334 |
| conversation | lru | 0.90 | 0.185 [0.184, 0.187] | 0.946 [0.924, 0.967] | 0.212 | 286 |
| conversation | lru | 0.95 | 0.146 [0.145, 0.147] | 0.781 [0.767, 0.796] | 0.174 | 237 |

Plant 2 default (all, costaware, tau 0.85): {"cache_hit_max": 0.21132753559565265, "cache_hit_max_ci95": [0.20997135471124515, 0.21270635485731226], "cache_half_mb": 1.1940711592760787, "cache_half_mb_ci95": [1.1484268531507573, 1.239494183478246], "cache_half_mb_knob_unit": 0.4132324456817934}

