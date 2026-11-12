# research/cachereplay — programme 2, WP6: cache behaviour from real prompts

LMSYS-Chat-1M first user turns are embedded with one fixed open encoder
(`sentence-transformers/all-MiniLM-L6-v2`, 384-d) and replayed through a
Python mirror of the gateway's semantic cache, per tenant, over a grid of
capacity C (entries per tenant) and threshold tau; hit correctness is measured
on Quora Question Pairs and read from the earlier LMSYS response-agreement
proxy; bytes per entry are measured on the real Go structure; saturating
curves h(c) = hmax * c / (c + half) are fitted per workload class with
bootstrap intervals over tenants and written as lookup tables for plant 2.
Exploratory until a registration says otherwise.

| file | what it is |
|---|---|
| `engine.py` | the eviction policies (cost-aware W28, LRU; FIFO for the old protocol) and the cache.go helpers (key text, doc ID, Store's cost estimate, threshold default) |
| `cachesim.py` | `TenantCache` (one tenant partition of `SemanticCache`), `NeighborIndex` (exact top-k earlier prompts, so a lookup scans 64 candidates and falls back to an exact scan), `replay_stream` (the gateway's request path) and `replay_insert_then_query` (the old protocol) |
| `streams.py` | first user turns in file order, key text, workload classes, tenant streams, the on-disk vector cache under `data/` |
| `replay.py` | steps 1-4: grid of C x tau x policy per class, outputs `replay.csv`, `summary.csv`, `replay.json`, `REPLAY.md` |
| `precision.py` | step 5: QQP precision-recall of the threshold rule (`qqp_pr_curve.csv/.png`), wrong-hit rate per tau, the LMSYS proxy read from `research/analysis/CACHE_PRECISION.md` |
| `oldcheck.py` | the check: the old study's own functions (loader, encoder wrapper, `nearest_at_sizes`) against the published table, and the WP6 engine against those functions query by query |
| `fit.py` | steps 6-7: MB per entry, fits per class/policy/tau in three units, `plant2_cache.json`, `FIT.md` |
| `gocheck/main.go` | Go evidence (build tag `cachereplay`, so normal builds, vet and tests never see it): `parity` traces of the real policies and the real `SemanticCache` on seeded scripts (`testdata/go_parity.json`); `mem` heap bytes per entry through the real `Store` path |
| `run_all.py` | every stage in one command |
| `estimate.py` | the runtime estimate below, from CPU-time measurements on the smoke data |
| `report.py` | markdown for the outputs |
| `test_*.py` | 41 tests (below) |

## The Go semantics mirrored

- `internal/ai/gateway/cache.go`: `cacheKeyText` (the embedded text is `"user: <prompt>\n"` for a one-message request), `NewSemanticCache` (threshold outside (0, 1] -> 0.95), `SemanticCache.Lookup` (hit iff the best score >= threshold; a hit calls `OnHit`), `Store` (cost = len(completion) bytes / 4 / 1000 x $0.0004; the chat handler in `server.go` calls `Store` after every miss), `StoreWithCost` (zero byte budget admits nothing; density = best score before the insert if > 0; doc ID = hex of sha256(key text)[:16]; `Upsert` replaces by ID; `OnStore`), `accountStoreLocked`, `overBoundLocked` and `evictWhileOverLocked` (evict while count > capacity, capacity 0 = unbounded, or while completion bytes exceed a set budget), `SetTenantBudgetBytes`.
- `internal/ai/vector/index.go` `Index.Search` (exact cosine; score descending, then doc ID ascending), `internal/ai/embed/embed.go` `Cosine` and `Normalize`.
- `internal/ai/gateway/eviction/costaware.go` (`OnStore`, `OnHit`, `OnRemove`, `Victim`, `weight`, default config: staleness half-life 6 h, hit-rate half-life 1 h, density epsilon 0.5; GreedyDual inflation) — the gateway's default policy (`policyFor`); `eviction/policy.go` `lru` as the comparison.

Stated differences: a virtual clock replaces `time.Now()`; an exact tie in w
(Go: map order, i.e. random) goes to the smaller doc ID; the fast cost-aware
scan evaluates w in a factored form (a scalar copy in Go's order is used by
the tests); candidate scores come from float32 products and every candidate
within 1e-5 of the best is re-scored in float64 before the threshold test.

## How to run

```bash
cd research/cachereplay
python run_all.py --preset smoke                 # every stage on <= 2,000 texts each (gomem needs Go)
python run_all.py --preset full --workers 6      # the full run (not run yet)
python -m pytest -q                              # 41 tests, about 15 s
```

From the repository root, to regenerate the Go traces the tests compare against:
`go run -tags cachereplay ./research/cachereplay/gocheck parity > research/cachereplay/testdata/go_parity.json`.

`full` = all first turns (about 1,000,000), classes `all`, `conversation`,
`code`, tenant streams of 32,768 prompts (30, 27 and 3 tenants), C = 64 x 2^0..8
(64 to 16,384) and unbounded, tau 0.75-0.95 in steps of 0.05, both policies,
QQP train + validation (404,276 labelled pairs, about 537,000 questions), the
old 200,000-turn reservoir. Stages can be skipped (`--skip precision,oldcheck`)
and the stream shortened (`--limit 300000`).

### Runtime estimate (`out/smoke/ESTIMATE.md`)

Measured in process CPU time on single-thread samples while another campaign
loaded the machine (so on the high side), projected at 4.5 core-equivalents
(i5-1135G7, 4 cores / 8 threads):

| stage | CPU h | wall h |
|---|---|---|
| embed 1,000,000 LMSYS first turns (0.10 CPU-s each, 67 tokens mean) | 28.0 | 6.2 |
| replay grid (60 tenants x 2 policies x 5 tau x 10 C; evictions bounded as if every lookup missed) | 3.8 | 0.9 |
| embed about 537,000 QQP questions | 2.7 | 0.6 |
| old check: embed the 200,000-turn reservoir; old NN + engine | 6.3 | 1.5 |
| loader, Go heap measurement, fit | 0.1 | 0.1 |
| **total** | | **about 9 h** |

Vectors are cached under `data/` (about 3 GB for the full stream: per-file
caches plus one combined file the replay workers map), so a second run of the
replay or the fit skips the 6 h of embedding. Peak memory was not measured;
the largest holders are the stream's vectors (1.5 GB) and the old check's
loader, which reads one 250 MB parquet file at a time into pandas.

## Outputs (aggregates only)

`out/<preset>/`: `REPLAY.md`, `summary.csv` (pooled per class/policy/tau/C:
warm-half hit rate with a 95% bootstrap interval over tenants, share of
completion bytes served, resident entries and bytes per entry, filled share),
`replay.csv` (per tenant), `PRECISION.md` + `precision.json` + the QQP curve,
`OLD_CHECK.md`, `gomem.json`, `FIT.md`, `plant2_cache.json`, `ESTIMATE.md`.

`plant2_cache.json` is what plant 2 reads: `plant2` holds `cache_hit_max`,
`cache_half_mb` and their intervals for the default (class `all`, cost-aware,
tau 0.85, the old curve's threshold); `curves[class][policy][tau]` holds the
fits in three units (`mb`: MiB of resident gateway heap, plant 2's unit since
its memory cost is cache_mb x price; `knob_mb`: MiB of completion bytes, the
unit the live knob `cache_size_mb` enforces through `SetTenantBudgetBytes`;
`entries`) and the measured points (the lookup table: C, entries, MB, hit
rate, filled); `wrong_hit_rate` holds QQP and LMSYS-proxy wrong-hit rates per
tau. `research/plant2/plant.py` is not edited: wiring it in is a one-line read
of `plant2` that replaces the 0.285 / 19.25 MB constants.

## Smoke results (2,000 first turns; 8 tenants of 250 prompts; not for plant 2)

- Warm-half hit rate, class `all`, unbounded: 0.146 / 0.125 / 0.094 / 0.063 / 0.048 at tau 0.75 / 0.80 / 0.85 / 0.90 / 0.95.
- Cost-aware hits less often than LRU at most capacities (tau 0.85, C = 64: 0.027 vs 0.064; tau 0.95, C <= 64: 0.000 vs up to 0.031) while serving a similar or larger share of completion bytes at small C: it evicts cheap and dense (near-duplicate) entries first. To be confirmed at full scale.
- Bytes per entry (Go 1.26 heap, real `Store` path): 2,384 B beyond key and completion bytes (1,536 B of it the vector); LMSYS first turns average 326 B of key and 774 B of completion, so about 3.48 KB per entry (the old constant assumed 3 KB). The live knob counts only completion bytes, 4.5 times fewer.
- QQP (500 validation pairs, gateway key text): precision 0.62 / 0.66 / 0.73 / 0.72 / 0.84 at tau 0.75-0.95, average precision 0.73; LMSYS proxy wrong-hit rate 0.73 / 0.71 / 0.69 / 0.67 / 0.66.
- Old protocol: the engine equals the old functions on a 600-turn reservoir (largest score difference 1.7e-15, no hit-count difference); the published numbers are compared by `--preset full`.

## Decisions (stated, not measured)

- File order is not time order: llama-2-13b-chat (released July 2023) has the same share (0.029-0.031) in every tenth of the files, so the data are shuffled. Tenant streams are contiguous chunks of the file order within a class (random samples), and the replay has no temporal locality; real traffic has some, so small-cache hit rates here are conservative for recency-friendly policies.
- Hit rates depend on the stream length per tenant (more history, more repeats); plant 2's h(c) does not. Full run: 32,768 prompts per tenant; the warm half is the second half; fits use only capacities that were full when the warm half began.
- Virtual clock 1.2 s per request per tenant (eviction/bench.go's "LMSYS-like inter-arrival"); it sets the cost-aware staleness and hit-rate terms.
- Key text: the gateway's `"user: <prompt>\n"` (the old study embedded the bare prompt; `--key-format raw` is available).
- Classes: `code` is a stated regex on the prompt (code syntax, a language or tool name, code/coding/programming), 11% of the smoke sample; `conversation` is the rest; `english` / `other_language` from the dataset's column (smoke only by default).
- Cache MB = MiB (2^20 bytes), as the gateway's knob.

## Not done

- The full run (needs about 9 h of an idle machine; command above).
- Wrong-hit rates on LMSYS are the published proxy (all turns, raw text, unbounded static cache), not re-measured on first turns with the gateway key text.
- Byte-budget capacities (the engine mirrors them and the tests cover them; the grid is in entries, as the plan says).
- The tests are not in CI (`.github/workflows/ci.yml` lists test directories explicitly) and plant 2 does not read `plant2_cache.json` yet.
- MiniLM is an English encoder; 22% of the smoke sample is not English, and those similarities are less meaningful (`--classes all,conversation,code,english,other_language` reports them at full scale).

Data: LMSYS-Chat-1M's licence forbids redistributing its text, so nothing here
writes prompts; `data/` (vectors, byte lengths, doc-ID hashes, the QQP files)
is git-ignored. The QQP parquet files are downloaded once from the Hugging
Face `nyu-mll/glue` repository, size-capped, checked to be parquet and read
with pyarrow only.
