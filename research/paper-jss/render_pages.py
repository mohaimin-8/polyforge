"""Render selected pages of main.pdf to PNG for a visual check (not part of the build)."""
import sys
from pathlib import Path

import fitz

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
doc = fitz.open(Path(__file__).with_name("main.pdf"))
pages = [int(p) for p in sys.argv[2:]] or range(1, doc.page_count + 1)
for n in pages:
    doc[n - 1].get_pixmap(dpi=70).save(out / f"p{n:02d}.png")
print(doc.page_count, "pages; rendered", list(pages))
