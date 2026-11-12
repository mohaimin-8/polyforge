# research/population — programme 2, work package WP8

Workloads as a population (plan: `Thesis Project/PolyForge_Q1_Research_Plan_v2_detailed_2026-11-12.md`,
outside the repository). Exploratory until a registration says otherwise.
The traces carry no tenant identity: every tenant here is a construction from
real request streams, and every output says so.

| file | what it is |
|---|---|
| `features.py` → `FEATURES.md`, `data/windows_{600,3600}s.csv.gz` | per-window features of six real streams (BurstGPT v1.1 by model and log type; Azure LLM 2024 conversation and code): rate, dispersion, p99 ratio, autocorrelation, token lengths, daily amplitude, session lengths |
| `generator.py` | a two-state Markov-modulated Poisson process, moment-matched to one window |
| `native.py` → `data/counts_10s.npz` | native replay: a tenant's demand from real 10 s counts of its stream (days superposed at their own time of day, thinned exactly to the tenant's rate); harness tenant mix `native` |
| `population.py` → `POPULATION.md`, `data/population.json` | 240 workloads x 8 tenants drawn from real windows, split once into tuning and evaluation halves; the check against the real windows' features |
| `test_*.py` | the generator recovers the moments it matches; native replay hits each target with real arrivals; harness integration (own and native mixes, validation) |

The raw traces live in `research/traces/data/` (not committed; see
`research/traces/README.md` for the sources).

```bash
python features.py      # a few minutes; reads about 2 GB of CSV with DuckDB
python native.py        # the 10 s counts native replay needs
python population.py
python -m pytest -q
```
