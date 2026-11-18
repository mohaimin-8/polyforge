# Data and model licences (programme 2, WP16)

What the artifact may contain and what a reader fetches by script. Licences read on 2026-11-14 from the
sources named (GitHub's licence API, the Hugging Face API's card metadata, the dataset pages).

## Datasets

| dataset | used by | licence | in the repository | how to get it |
|---|---|---|---|---|
| BurstGPT (HPMLL/BurstGPT) | population, native replay, comparators | CC-BY-4.0 (repository LICENSE); cite the KDD '25 paper, doi:10.1145/3711896.3737413 | derived counts and features only (`research/population/data/`; per-600 s request rates of programme 1's trace campaigns in `eval/harness/trace_cache/`) | `python research/traces/fetch_burstgpt.py` (release v2.0) |
| Azure LLM inference trace 2024 (AzurePublicDataset) | population, native replay, WP11 cells | CC-BY; cite DynamoLLM, HPCA 2025 | derived counts and features only | `research/traces/etl_azure_llm.py` downloads the release assets |
| Azure Functions trace 2019 (AzurePublicDataset) | WP17 workloads | CC-BY; cite "Serverless in the Wild", USENIX ATC 2020 | the two derived per-minute windows (`research/hvpa/data/`) | release `dataset-functions-2019`, then `python -I research/hvpa/azure_workloads.py <d01 csv>` |
| LMSYS-Chat-1M (lmsys/lmsys-chat-1m) | cache replay (WP6), WP11 prompts | gated; its own dataset licence agreement forbids redistribution | never: no prompt text, only byte lengths, doc-ID hashes in git-ignored caches and aggregates | accept the terms on Hugging Face, then `huggingface-cli download lmsys/lmsys-chat-1m` |
| RouterBench (withmartian/routerbench) | WP7 mode A tier prices and quality | code MIT (GitHub); the dataset card names no licence | per-tier summaries only (`research/tierquality/tier_summary.json`) | `research/tierquality/tiers.py` (pickles loaded with the whitelist unpickler `safe_load.py`, SHA-256 checked) |
| GLUE QQP (nyu-mll/glue, qqp) | cache precision (WP6) | "other" on its card (Quora's terms) | precision tables only | `research/cachereplay/precision.py` fetches the parquet files |
| ARC-Challenge (allenai/ai2_arc) | WP7 mode B answer quality | CC BY-SA 4.0 | accuracies only (`research/tierquality/selfhosted_summary.json`); the per-question answers stay git-ignored | `python research/tierquality/selfhosted.py fetch` |

## Models

| model | used by | licence |
|---|---|---|
| Qwen2.5-0.5B-Instruct, Qwen2.5-1.5B-Instruct, Qwen2.5-7B-Instruct (GGUF Q4_K_M) | testbed tiers, WP7 | Apache-2.0 |
| Qwen2.5-3B-Instruct (GGUF Q4_K_M) | WP7, the testbed's optional `mid3b` set | Qwen Research License (not Apache-2.0) |
| sentence-transformers/all-MiniLM-L6-v2 (revision 1110a243) | the gateway's cache encoder, WP6 | Apache-2.0 |

Model files are never in the repository. `testbed/k8s/base/models.lock` pins each file by size and
sha256 with a commit-pinned URL, and `research/tierquality/selfhosted.py` names the files it reads.
