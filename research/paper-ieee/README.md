# ECCE 2027 paper (IEEE conference format)

2027 International Conference on Electrical, Computer and Communication
Engineering (ECCE), CUET. IEEEtran conference template, A4, two columns,
6 pages including references, double-blind review.

## Files

- `main.tex` – the paper
- `IEEEtran.cls` – IEEE class file
- `figures/*.pdf`, `tables/*.tex` – figures and generated tables
- `POLYFORGE_IEEE_submission.pdf` – double-blind review copy (upload this to CMT)
- `POLYFORGE_IEEE_camera_ready_preview.pdf` – preview of the final version with authors
- `REVIEW_RESPONSE.md` – point-by-point response to the internal and external reviews
- `CHANGES_round10.pdf` – the tenth round's style edits (30 Oct 2026), marked
  against the ninth-round version (red removed, blue added); numbers,
  citations, test IDs, equations, tables and figures are unchanged
- `CHANGES_round11.pdf` – the eleventh round (31 Oct 2026): the second
  external review and three registered follow-up experiments, marked against
  the tenth-round version. Unlike the style rounds, this round adds results:
  the plant's latency model (Eq. 2), the live-fitted and wide-step follow-ups
  (`RESULTS_REVIEWER_FOLLOWUP.md`), the real BurstGPT replay against the
  audited comparators (`RESULTS_TRACE_AUDITED.md`) and the updated programme
  counts. See "Eleventh round" in `REVIEW_RESPONSE.md`.

## Build

```bash
pdflatex main.tex && pdflatex main.tex
```

On Overleaf, upload `main.tex`, `IEEEtran.cls`, `figures/` and `tables/`.

## Switches (top of `main.tex`)

| Setting | Result |
|---|---|
| `\blindtrue` (default) | Review copy: no authors, links or system names (SysX / JC) |
| `\blindfalse` + `\camerareadytrue` | Final version: authors, PolyForge / JCAC, archive links, ECCE header and IEEE copyright line |

Both versions build to 6 pages. The camera-ready version fits with
`\copyrightline` still empty; if the copyright line or the e-mail addresses
push it over, cut repetition, not qualifiers or conditions.

## Before the camera-ready version

- Put the copyright line ECCE sends with its camera-ready instructions into
  `\copyrightline` (near the author block in `main.tex`).
- Add author e-mail addresses to the author blocks.
- Regenerate `figures/fig_tradeoff.pdf` so its labels read "JCAC" instead of "JC".
