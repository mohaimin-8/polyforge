# IEEE conference paper

`main.tex` is the PolyForge conference paper in the IEEE conference template
(`IEEEtran.cls`, conference mode, two columns, 6 pages including references).

## Build

```bash
pdflatex main.tex && pdflatex main.tex      # twice, for cross-references
```

No BibTeX run is needed: the bibliography is written in `main.tex`, as in the
template. On Overleaf, upload `main.tex`, `IEEEtran.cls` and `figures/*.pdf`.

## Review mode

Line 20 of `main.tex` holds the switch. `\blindfalse` prints the authors and
the repository, archive and registration links (single-blind review and
camera-ready). `\blindtrue` hides all of them (double-blind review).

Before submitting, resolve the `TODO(author)` comments: the e-mail
addresses and the author list and order.

## Where the numbers come from

Every number in the paper is a value a committed record reports, under
`research/analysis/`: `RESULTS_FAIR_J.md`, `RESULTS_RETUNED.md`,
`EQUIVALENCE_FAIR.md`, `REANALYSIS_MATRIX.md`, `RESULTS_EVICTION_PARITY.md`,
`RESULTS_JOINTNESS.md`, `RESULTS_ACTUATION.md`, `RESULTS_QUALITY.md`,
`RESULTS_HELDOUT.md`, `RESULTS_WIDTH.md`, `RESULTS_STRUCTURAL_MISMATCH.md`,
`SENSITIVITY_FAIR.md`, `HYPOTHESIS_LEDGER.md` and `PLANNER_SCALING.md`. The
hypothesis tags in the text (`FJ-H1`, `WD-H4`, ...) name the registered test.

## Figures

```bash
python make_figures.py
```

This redraws `figures/fig_chain.pdf` (fig22's tracks A and B) and
`figures/fig_width.pdf` (fig23) at IEEE column widths. It takes every number
from the gated figure scripts' own functions (`fig_adjudication_chain.py`,
`fig_width.py`), which use the frozen PREREG scorers. It refuses to save if
any plotted number differs from the committed figure sheets
(`FIGURE_ADJUDICATION_CHAIN.md`, `FIGURE_WIDTH.md`), and it writes those
numbers to `figures/plotted_numbers.json`.
