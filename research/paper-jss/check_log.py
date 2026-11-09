"""Report overfull boxes in main.log with the source file they occur in."""
import re
from pathlib import Path

log = Path(__file__).with_name("main.log").read_text(encoding="latin-1")
pat = re.compile(r"Overfull \\hbox \(([\d.]+)pt too wide\) in (?:paragraph|alignment) at lines (\d+--\d+)")
for m in pat.finditer(log):
    pre = log[: m.start()]
    stack = []
    for tok in re.finditer(r"\(\./([\w\-/.]+\.tex)|\)", pre):
        if tok.group(1):
            stack.append(tok.group(1))
        elif stack:
            stack.pop()
    print(f"{m.group(1):>9}pt  lines {m.group(2):<9} {stack[-1] if stack else 'main.tex'}")
print("pages:", re.search(r"Output written on main.pdf \((\d+) pages", log).group(1))
