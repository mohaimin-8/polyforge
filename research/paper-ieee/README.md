# ICEEICT 2027 paper (IEEE conference format)

7th International Conference on Electrical Engineering and Information &
Communication Technology (ICEEICT 2027), MIST, Dhaka. IEEEtran conference
template, A4, two columns, 6 pages including references, double-blind review.
Deadline and rules: `SUBMISSION.md`.

## Files

- `main.tex` – the paper
- `IEEEtran.cls` – IEEE class file
- `figures/*.pdf`, `tables/*.tex` – figures and generated tables (`make_assets.py`)
- `POLYFORGE_IEEE_submission.pdf` – double-blind review copy (upload this to CMT)
- `POLYFORGE_IEEE_camera_ready_preview.pdf` – preview of the final version with authors
- `REVIEW_RESPONSE.md` – point-by-point responses to the internal and external reviews
- `CHANGES_round11.pdf` – the eleventh round (31 Oct 2026): the second
  external review and three registered follow-up experiments, marked against
  the tenth-round version. See "Eleventh round" in `REVIEW_RESPONSE.md`.
- `CHANGES_round12.pdf` – the twelfth round (9 Nov 2026): the ICEEICT audit,
  marked against the eleventh-round version. This round adds results from new
  registered experiments (CS: three cache-hit curves; BM: the comparators
  under the joint controller's budget; SA: request-level shed accounting), the
  cost-normalisation check, the tier table, the two research questions and the
  count reconciliation. See "Twelfth round" in `REVIEW_RESPONSE.md`.

## Build

```bash
python make_assets.py            # tables and figures from the committed records
pdflatex main.tex && pdflatex main.tex && pdflatex main.tex
```

On Overleaf, upload `main.tex`, `IEEEtran.cls`, `figures/` and `tables/`.

## Switches (top of `main.tex`)

| Setting | Result |
|---|---|
| `\blindtrue` (default) | Review copy: no authors, links or system names (SysX / JC) |
| `\blindfalse` + `\camerareadytrue` | Final version: authors, PolyForge / JCAC, archive links, ICEEICT header and IEEE copyright line |

Both versions build to 6 pages, with the conclusion in the left column of
page 6. If the copyright line or the e-mail addresses push the camera-ready
version over, cut repetition, not qualifiers or conditions.

## Before the camera-ready version

- Put the copyright line ICEEICT sends with its camera-ready instructions into
  `\copyrightline` (near the author block in `main.tex`).
- Add author e-mail addresses to the author blocks.
