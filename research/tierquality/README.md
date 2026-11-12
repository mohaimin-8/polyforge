# research/tierquality — programme 2, work package WP7 (mode A)

Per-request price and answer quality of the three model tiers of plant 2's
mode A (API-backed), from RouterBench (Hu et al., arXiv:2403.12031v2): 36,497
prompts with every answer's score and estimated cost for 11 LLMs.

| file | what it is |
|---|---|
| `safe_load.py` | loads RouterBench's pickles with an unpickler that admits only the pandas and numpy reconstruction functions the files reference (found by opcode inspection, without executing them), after checking the SHA-256 Hugging Face publishes |
| `tiers.py` → `TIERS.md`, `tier_summary.json` | mean cost and quality per model (0-shot and 5-shot files); small, mid and large chosen by a cost rule fixed before the result |

`plant.py` (research/plant2) reads `tier_summary.json` for mode A's tier
prices and qualities. The costs are RouterBench's March 2024 estimates (API
prices then; open models at Together AI's), dated, not current. The dataset
states no licence, so the per-prompt table is not committed; the raw files go
in `data/` (git-ignored):

```bash
mkdir -p data && cd data
curl -LO https://huggingface.co/datasets/withmartian/routerbench/resolve/main/routerbench_0shot.pkl
curl -LO https://huggingface.co/datasets/withmartian/routerbench/resolve/main/routerbench_5shot.pkl
cd .. && python tiers.py
```

Mode B's tier quality (self-hosted Qwen2.5 0.5B/3B/7B) still uses the model
cards' benchmark means; the plan's own measurement (500-1,000 benchmark
questions per served model) is pending.
