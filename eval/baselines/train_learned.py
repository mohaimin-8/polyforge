"""Offline training for the learned joint controller (PREREG_LEARNED_CONTROL).

The learned baseline is the RL analog of PolyForge's MPC: a shared,
tenant-agnostic Q-table over the full replicas×cache×tier action space
(`research/jcac_sim/baselines.py::LearnedJointController`). Unlike the
reactive baselines — which learn nothing and are tuned online by
`baselines/tune.py` — an RL controller is *trained offline and deployed
frozen*, so its hyperparameter selection is a train/val/test protocol, not
an online sweep. This script is that protocol, frozen before the campaign
run (the pre-registration is the anchor):

  1. TRAIN: accumulate one shared Q-table across many demand episodes drawn
     from the workload space with TRAIN seeds, ε decaying eps0→eps1.
  2. VAL:   for each hyperparameter combo, deploy the trained policy
     frozen-greedy (train=False, ε=0) on a VAL slice with VAL seeds
     (disjoint from TRAIN and from the eval matrix), scored on the paper's
     own J. Lowest-J combo wins — the W34 "tune on a disjoint slice, never
     on the eval cells" rule, applied to the offline policy.
  3. FIT:   retrain the winning combo on TRAIN and write the committed
     artifact `research/results/learned_qtable.json`, which
     `systems.py::learned_trained` deploys on the eval matrix.

Nothing here ever touches the eval-matrix seeds. Run from eval/:
    python baselines/train_learned.py          # full protocol -> artifact
    python baselines/train_learned.py --smoke   # tiny budget, wiring check
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import sys
import tempfile
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EVAL_DIR.parent
sys.path.insert(0, str(EVAL_DIR))

from harness import workloads  # noqa: E402  (also wires the sim path)
import baselines  # noqa: E402
import simulate  # noqa: E402
from model import TenantState  # noqa: E402

ALPHA, BETA, GAMMA = 1.0, 2.0, 0.5
COST_SCALE_USD = 0.01  # controller.COST_SCALE_USD, the shared normalizer
JITTER_SALT = 0x5F3759DF  # matches sim_backend._JITTER_SALT

ARTIFACT = REPO_ROOT / "research" / "results" / "learned_qtable.json"
GRID_CSV = Path(__file__).resolve().parent / "grids" / "learned_train.csv"

# --- Frozen protocol constants (the pre-registration) -----------------------
WORKLOADS = ["crud_bursty", "crud_steady", "ai_cacheable", "ai_uncacheable", "agentic"]
MIXES = ["uniform", "premium_heavy", "besteffort_heavy", "whale"]
SIZES = ["small", "medium", "large"]
# Validation slice: representative, disjoint-seeded, never the eval matrix.
VAL_MIXES = ["uniform", "whale"]
VAL_SIZE = "medium"

TRAIN_SEED_BASE = 51_2026  # disjoint region from TUNING_SEED (91_2026) and run_ids
VAL_SEED_BASE = 61_2026
STEPS = 160
EPS0, EPS1 = 0.4, 0.02  # linear ε decay across the ordered training episodes

# Hyperparameter grid tuned on VAL (the RL analogs of the reactive grids).
GRID = {
    "learning_rate": [0.2, 0.3],
    "replay_batch": [8, 16],
    "reward_beta": [2.0, 3.0],
}
# Frozen non-swept RL constants.
DISCOUNT = 0.7
REPLAY_SIZE = 4000


def _seed(base: int, *parts) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return base + int(digest[:8], 16) % 100_000


def _train_cells(train_seeds: list[int], passes: int):
    cells = [(wl, mix, sz) for wl in WORKLOADS for mix in MIXES for sz in SIZES]
    episodes = [(wl, mix, sz, s) for (wl, mix, sz) in cells for s in train_seeds]
    return episodes * passes


def _val_cells(val_seeds: list[int]):
    return [(wl, mix, VAL_SIZE, s)
            for wl in WORKLOADS for mix in VAL_MIXES for s in val_seeds]


def train(hp: dict, train_seeds: list[int], passes: int, steps: int) -> dict:
    """Accumulate one shared Q-table across the training episodes with ε decay."""
    shared_q: dict = {}
    episodes = _train_cells(train_seeds, passes)
    total = max(1, len(episodes) - 1)
    for i, (wl, mix, sz, seed) in enumerate(episodes):
        eps = EPS0 + (EPS1 - EPS0) * (i / total)
        rseed = _seed(TRAIN_SEED_BASE, wl, mix, sz, seed)
        tenant_ids, buckets, configs, limits = workloads.build(wl, mix, sz, rseed, steps)
        ctl = baselines.LearnedJointController(
            configs, shared=True, train=True, epsilon=eps,
            learning_rate=hp["learning_rate"], discount=DISCOUNT,
            reward_beta=hp["reward_beta"], replay_batch=hp["replay_batch"],
            replay_size=REPLAY_SIZE, seed=rseed)
        ctl._shared_q = shared_q  # accumulate in place across episodes
        jb = simulate.jitter_buckets(buckets, rseed ^ JITTER_SALT)
        states = {tid: TenantState() for tid in tenant_ids}
        for demand in jb:
            states = ctl.plan(states, demand)
    return shared_q


def val_score(shared_q: dict, val_seeds: list[int], steps: int, tmp: Path) -> float:
    """Deploy the frozen policy greedily on the VAL slice; mean J (lower better)."""
    holder = baselines.LearnedJointController({}, shared=True)
    holder._shared_q = shared_q
    holder.save_q(str(tmp))
    total, n_cells = 0.0, 0
    for wl, mix, sz, seed in _val_cells(val_seeds):
        rseed = _seed(VAL_SEED_BASE, wl, mix, sz, seed)
        tenant_ids, buckets, configs, limits = workloads.build(wl, mix, sz, rseed, steps)
        r = simulate.run(
            "learned", tenant_ids, buckets, configs=configs, limits=limits,
            collect_rows=False, jitter_seed=rseed ^ JITTER_SALT,
            controller_params={"train": False, "epsilon": 0.0, "shared": True,
                               "qtable_path": str(tmp), "seed": rseed})
        n = len(tenant_ids)
        cost_norm = r.total_cost_usd / max(1, r.steps * n) / COST_SCALE_USD
        total += ALPHA * cost_norm + BETA * r.mean_violation + GAMMA * (1.0 - r.mean_jain)
        n_cells += 1
    return total / max(1, n_cells)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true",
                    help="tiny budget + temp artifact: verify wiring, do not "
                         "overwrite the committed Q-table")
    args = ap.parse_args()

    if args.smoke:
        train_seeds, val_seeds, passes, steps = [1], [9], 1, 40
        grid = {"learning_rate": [0.3], "replay_batch": [8], "reward_beta": [2.0]}
        artifact = Path(tempfile.gettempdir()) / "learned_qtable_smoke.json"
        grid_csv = Path(tempfile.gettempdir()) / "learned_train_smoke.csv"
    else:
        train_seeds, val_seeds, passes, steps = [1, 2, 3], [7, 8], 2, STEPS
        grid = GRID
        artifact = ARTIFACT
        grid_csv = GRID_CSV

    names = sorted(grid)
    combos = [dict(zip(names, v)) for v in itertools.product(*(grid[n] for n in names))]
    print(f"== train_learned: {len(combos)} combos, "
          f"{len(_train_cells(train_seeds, passes))} train eps x {steps} steps, "
          f"{len(_val_cells(val_seeds))} val eps each ==")

    tmp = Path(tempfile.gettempdir()) / "learned_val_q.json"
    rows, best, best_q, best_j = [], None, None, float("inf")
    for hp in combos:
        t = time.time()
        q = train(hp, train_seeds, passes, steps)
        j = val_score(q, val_seeds, steps, tmp)
        rows.append({**hp, "val_J": round(j, 5), "states": len(q),
                     "wall_s": round(time.time() - t, 1)})
        marker = ""
        if j < best_j:
            best, best_q, best_j, marker = hp, q, j, "  <- best"
        print(f"  {hp} val_J={j:.4f} states={len(q)}{marker}")

    grid_csv.parent.mkdir(exist_ok=True)
    with open(grid_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # Refit the winner on TRAIN and write the deployable artifact.
    final_q = train(best, train_seeds, passes, steps)
    holder = baselines.LearnedJointController({}, shared=True)
    holder._shared_q = final_q
    artifact.parent.mkdir(parents=True, exist_ok=True)
    holder.save_q(str(artifact))
    print(f"best {best} val_J={best_j:.4f}; artifact -> {artifact} "
          f"({len(final_q)} states); grid -> {grid_csv.name}")


if __name__ == "__main__":
    main()
