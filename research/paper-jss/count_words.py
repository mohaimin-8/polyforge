"""Word count of the abstract and character counts of the highlights."""
import re
from pathlib import Path

here = Path(__file__).resolve().parent
tex = (here / "main.tex").read_text(encoding="utf-8")
abstract = tex.split("\\begin{abstract}")[1].split("\\end{abstract}")[0]
abstract = re.sub(r"\\[a-zA-Z]+", " ", abstract).replace("\\%", "%")
print("abstract words:", len(abstract.split()))
hl = here / "highlights.txt"
if hl.exists():
    for line in hl.read_text(encoding="utf-8").splitlines():
        if line.startswith("- "):
            print(f"{len(line[2:]):3d}  {line[2:]}")
