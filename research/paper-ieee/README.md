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
- `REVIEW_RESPONSE.md` – point-by-point response to the internal review
- `CHANGES_vs_original.pdf` – the readability edits of 30 Oct 2026, marked
  against the earlier text (red removed, blue added); numbers, citations,
  test IDs, equations, tables and figures are unchanged
- `CHANGES_IEEE_tense_voice.pdf` – the IEEE tense and voice pass (seventh
  round, 30 Oct 2026), marked against the readability version (red removed,
  blue added); numbers, citations, test IDs, equations, tables and figures
  are unchanged
- `CHANGES_style.pdf` – the style pass (eighth round, 30 Oct 2026),
  marked against the IEEE tense and voice version; same guarantees
- `CHANGES_round10.pdf` – the third style pass (tenth round, 30 Oct 2026),
  marked against the ninth-round version; same guarantees

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
