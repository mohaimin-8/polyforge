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

## Before submission (human fill-ins)

1. `report/main.tex` — the `\thesisauthor`/`\thesisdegree`/`\thesisuniversity`/
   `\thesisdepartment`/`\thesissupervisor` macros are placeholders.
2. `report/references.bib` — entries for arXiv-only systems use placeholder
   author fields (e.g. "Chiron authors") because author lists were not
   verified from the papers; replace them with real author lists before any
   submission. URLs/arXiv IDs are from `docs/RELATED_WORK.md` (verified
   2026-07-12/13).
3. The live jcac ordinal figure (PHASE7) does not exist yet; the report
   states its status honestly in §6.10 (live section). After the live run,
   add the figure + frozen caption from `docs/PHASE7_JCAC_PLAN.md` and
   update that section.
4. Companion defense prep lives in `docs/DEFENSE_QA.md` (13 pre-answered
   questions) — rehearse from there.
