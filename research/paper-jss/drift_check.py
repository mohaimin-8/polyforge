"""Run the repository's withdrawn-claim patterns (scripts/thesis_drift.py,
CONTRADICTED) over this manuscript and report every match with its context,
so a withdrawn number never appears here bare. Read-only."""
import importlib.util
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("thesis_drift", HERE.parents[1] / "scripts" / "thesis_drift.py")
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)

hits = 0
for tex in sorted(HERE.glob("*.tex")) + sorted((HERE / "tables").glob("*.tex")):
    lines = tex.read_text(encoding="utf-8").splitlines()
    for row in drift.CONTRADICTED:
        claim, pattern = row[0], row[1]
        rx = re.compile(pattern, re.IGNORECASE)
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                hits += 1
                print(f"{tex.name}:{i}: [{claim}] {line.strip()[:110]}")
print(f"{hits} match(es) of {len(drift.CONTRADICTED)} withdrawn-claim patterns")
