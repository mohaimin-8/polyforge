# Thesis package (Milestone 9)

Two LaTeX documents, both compiled from the committed evidence base — every
number traces to a campaign file in `research/analysis/` (see the thesis's
Appendix A and `RESULTS_MASTER.md`).

| Artifact | Source | Build | Output |
|---|---|---|---|
| Thesis report (~43 pp.) | `report/main.tex` + `report/chapters/` | `report/build.ps1` (or `latexmk -pdf main.tex`) | `report/main.pdf` |
| Defense slides (16 frames) | `slides/slides.tex` | `slides/build.ps1` | `slides/slides.pdf` |

Both need a TeX distribution (built with MiKTeX 25.12 on Windows; any
TeX Live works). Figures are included directly from
`eval/results/figures/*.pdf` — do not move that directory relative to
`thesis/`.

## Status (2026-09-16)

The text was last written in session 23 (July 2026) and has not been
revised since; the evidence base has. **`docs/THESIS_DRIFT.md`** (generated
by `scripts/thesis_drift.py`, kept current by CI) lists exactly what has
moved: the claims the adjudications withdrew or bounded and every line of
tex that still makes them (the −70 %/−42 % cost headlines, the 4.3e-06
p-value, the d_z 0.66–1.12 matrix win, the +2884 % ablation), the records
no chapter cites (the 24 h soak, the multi-node sitting, the live plane
B1/B1′/B1″, the wire attack, the S3 floor, …), and the committed figures no
document includes. Rewrite from that list, not from memory. The figures the
documents do include (`fig01/03/07/08/09/13/16/17`) are unchanged since the
PDFs were built, so the compiled PDFs are current on figures and stale on
text.

## Before submission (human fill-ins)

1. `report/main.tex` — the `\thesisauthor`/`\thesisdegree`/`\thesisuniversity`/
   `\thesisdepartment`/`\thesissupervisor` macros are placeholders.
2. `report/references.bib` — author lists are complete (the two `and others`
   entries were filled from arXiv on 2026-09-16); URLs/arXiv IDs are from
   `docs/RELATED_WORK.md` (verified 2026-07-12/13). Re-verify venue fields
   for the arXiv-only entries at submission time.
3. `eval/zenodo_creators.json` — write it, rebuild the deposit
   (`cd eval && python scripts/archive_zenodo.py`), publish, and put the DOI
   in the Reproducibility section.
4. Companion defense prep lives in `docs/DEFENSE_QA.md` — rehearse from
   there, after it too has been checked against `THESIS_DRIFT.md`.
