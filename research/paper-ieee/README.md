# IEEE conference paper

`main.tex` is the PolyForge conference paper for ICEEICT 2027, in the IEEE
conference template (`IEEEtran.cls`, conference mode, A4, two columns, at most
6 pages including references). The page is A4, as the venue requires, with the
margins of the sample IEEE template (0.75 in top, 1 in bottom); IEEEtran's own
a4paper layout would leave a 1.69 in bottom margin. Every figure and table sits
inside one column.
`SUBMISSION.md` records the venue's rules and how the paper meets them.

## Build

```bash
pdflatex main.tex && pdflatex main.tex      # twice, for cross-references
```

No BibTeX run is needed: the bibliography is written in `main.tex`, as in the
template. On Overleaf, upload `main.tex`, `IEEEtran.cls`, `figures/*.pdf` and
`tables/*.tex`.

## Review and camera-ready modes

Two switches near the top of `main.tex` (after the comment block that lists
the venue's rules):

| switch | effect |
|---|---|
| `\blindtrue` (default) | double-blind submission: no authors, affiliations or links; the system and controller print as "SysX" and "JC" |
| `\blindfalse` | prints the authors, the real names (PolyForge, JCAC) and the code, archive and registration links |
| `\camerareadytrue` | adds the conference header and the IEEE copyright line on page 1; use with `\blindfalse` |

Both the blind version and the camera-ready version (`\blindfalse`,
`\camerareadytrue`) build to 6 pages. Before the camera-ready version, resolve
the `TODO(author)` comments: the e-mail addresses and the author list and order.

## Where the numbers come from

Every number in the paper is a value a committed record reports, under
`research/analysis/`: `RESULTS_FAIR_J.md`, `RESULTS_RETUNED.md`,
`EQUIVALENCE_FAIR.md`, `REANALYSIS_MATRIX.md`, `RESULTS_EVICTION_PARITY.md`,
`RESULTS_JOINTNESS.md`, `RESULTS_ACTUATION.md`, `RESULTS_QUALITY.md`,
`RESULTS_HELDOUT.md`, `RESULTS_WIDTH.md`, `RESULTS_STRUCTURAL_MISMATCH.md`,
`SENSITIVITY_FAIR.md`, `HYPOTHESIS_LEDGER.md` and `PLANNER_TIMING_SOLVERS.md`.
The hypothesis tags in the text (`FJ-H1`, `WD-H4`, ...) name the registered
test.

## Figures and tables

```bash
python make_assets.py
```

This writes the four data figures (`figures/fig_chain.pdf`, `fig_sizes.pdf`,
`fig_width.pdf`, `fig_tradeoff.pdf`) at IEEE column width and the generated tables
(`tables/tab_campaigns.tex`, `tab_arms.tex`, `tab_post.tex`; `tab_width.tex`
is kept for reference but not included). It takes every number from the
records, the run exports, or the gated figure scripts' own functions
(`fig_adjudication_chain.py`, `fig_width.py`, which use the frozen PREREG
scorers). It refuses to save if any plotted number differs from the committed
figure sheets (`FIGURE_ADJUDICATION_CHAIN.md`, `FIGURE_WIDTH.md`), and it
writes every value it used to `figures/plotted_numbers.json`. Fig. 1 (the system
workflow in Section III, Methodology) and Table IV (checklist) are written in
`main.tex` itself; `fig_sizes.pdf` and `tab_width.tex` are generated but no
longer included. `REVIEW_RESPONSE.md` answers the reviewer report point by point.
