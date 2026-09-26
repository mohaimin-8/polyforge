"""PolyForge evaluation harness (W33).

One YAML file describes an experiment: which systems, which workload
classes, which tenant mixes, which cluster sizes, how many repetitions.
The harness expands that into a run matrix with deterministic run IDs,
executes each run on a backend (`sim` locally, `cluster` against kind),
and lands every result in one DuckDB file with a standardized schema so
every experiment — smoke, full, ablation, reviewer-requested — is analyzed
by the same queries.

The simulator in research/jcac_sim is imported by path: it is a flat,
stdlib-only module tree and the single source of truth for the system
model. The harness never re-implements the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

# 1.1.0 (2026-09-26): runs.recorded_at is UTC on every host. Rows written by
# 1.0.0 hold the RECORDING HOST's local time, unmarked (laptop UTC+6, cloud
# UTC); scripts/check_prereg_timing.py reads them with that caveat.
HARNESS_VERSION = "1.1.0"
# Bump when the result schema or run-identity inputs change; it is part of
# every run_id, so old rows can never be silently mixed with new ones.
SCHEMA_VERSION = "1"

EVAL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EVAL_DIR.parent
SIM_DIR = REPO_ROOT / "research" / "jcac_sim"

if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))
