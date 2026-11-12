"""Path set-up for the programme-2 theory tests: this directory, the
simulator (research/jcac_sim) and the evaluation harness (eval/)."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
