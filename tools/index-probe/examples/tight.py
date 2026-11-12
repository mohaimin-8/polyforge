"""Worked example 1 (box mode): the T3 instance, where per-knob control
loses rho - eta of an index rho (here 0.75 of 1.0; the ratio tends to 1 as
eta -> 0, so Theorem 1's bound cannot be improved). `phi` is importable as `tight:phi`;
`python tight.py` writes `tight_box.json` next to this file.

    cd tools/index-probe/examples
    python tight.py
    python ../index_probe.py box --objective tight:phi --box tight_box.json
    python ../index_probe.py box --objective tight:phi --box tight_box.json --sample 59
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "research" / "theory"))

from interaction_index import tight_instance  # noqa: E402

RHO, ETA = 1.0, 0.25
VALUES, phi, ANCHOR = tight_instance(RHO, ETA)

if __name__ == "__main__":
    (HERE / "tight_box.json").write_text(
        json.dumps({"values": [list(v) for v in VALUES], "anchor": list(ANCHOR)}), encoding="utf-8")
    print(HERE / "tight_box.json")
