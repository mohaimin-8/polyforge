# Release checklist — what goes out with the paper, and who does it

Everything the paper attaches or points at, in one place, with the state it
is in. Items marked **done** are maintained by scripts and gates and need no
hand; items marked **human** need an account, a name, or a payment method
and cannot be automated from this repo. (Revised 2026-09-16; the July
version predated the evidence site, the AWS sittings and the caption gate.)

## 1. Figures and captions — done, gated

- `eval/results/figures/fig01`–`fig21` as vector PDF + 600-DPI PNG;
  `content/*.json` is what each one plots; `FIGURES.md` is every caption.
- `python scripts/reproduce.py` rebuilds all 21 and fails on any figure that
  plots different data, carries a different caption, or has no caption.
- The thesis/paper include `fig01/03/07/08/09/13/16/17` (unchanged since the
  PDFs were built). `docs/THESIS_DRIFT.md` §3 lists the 13 committed figures
  no document includes yet — a choice for the writing, not a defect.

## 2. Records and pre-registrations — done, gated

- 59 records regenerate byte-identically (`scripts/reproduce.py`); 21 more
  carry a stated reason in `UNGATED`; 48 pre-registrations; `check_preregs.py`
  proves no registered text moved after its result existed.
- `research/analysis/RECORDS_INDEX.md` (generated) enumerates all of them;
  `OSF_REGISTRATION.md` carries the 29 OSF rows.

## 3. Zenodo deposit — done, published 2026-09-17

- [x] `eval/zenodo_creators.json` (author as creator with ORCID
      0009-0003-3254-9532; supervisor as contributor, role Supervisor)
- [x] `cd eval && python scripts/archive_zenodo.py` — 1023 files, 49.8 MB,
      SHA-256 manifest, no placeholder
- [x] Published: **https://doi.org/10.5281/zenodo.22801196**
      (Islam, M. M. (2026). PolyForge evaluation artifact: pre-registered
      controller-comparison campaigns (harness, baselines, raw results,
      live-run data, IaC) [Dataset]. Zenodo.)
- [x] DOI in `README.md`, `docs/REPRODUCE.md`, the thesis (traceability
      appendix, discussion, `references.bib` entry `polyforge2026zenodo`)
- A new version (new DOI under the same concept) is needed only if the
      bundle changes — rebuild, upload as *New version*, update the DOI here.

## 4. Evidence site — done, needs the repo public

- `scripts/build_pages.py` renders every record, pre-registration and figure
  (with its caption) as a static site; `.github/workflows/pages.yml` deploys
  it. `scripts/test_build_pages.py` fails on a gated record without a page, a
  dead link, or two builds that differ.
- [ ] **human:** make the repository public (also unblocks CI minutes), enable
      GitHub Pages on the workflow, put the URL in the paper.

## 5. Images and chart — human (GHCR org)

- [ ] Build and push `ghcr.io/<user>/polyforge-operator` and
      `ghcr.io/<user>/polyforge-planner` (`release.yml` signs with cosign;
      align tags with the chart's `appVersion`)
- [ ] `helm package deploy/helm/polyforge-operator`; host the chart index on
      the Pages branch; register it on artifacthub.io
- [ ] Update `deploy/helm/polyforge-operator/values.yaml` if the GHCR org differs

## 6. Live evidence — done

The live sittings that the paper cites ran on rented hosts and are in the
gate as records: the 24 h CRUD-plane soak (`RESULTS_LIVE_SOAK_V8.md`), the
multi-node sitting (`RESULTS_MULTINODE.md`), the trace-driven cluster
(`RESULTS_TRACE_LIVE.md`), and the three-knob live plane B1/B1′/B1″
(`RESULTS_WAVE4_LIVE_PLANE.md`, `RESULTS_WAVE4_CALIBRATED.md`,
`RESULTS_WAVE4_DWELL.md`; runbook `WAVE4_RENTED_HOST_RUNBOOK.md`). Every box
is terminated and the AWS key is deactivated; nothing is running.

## 7. The text — rewritten to the records (2026-09-16)

`thesis/report` (62 pp.) and `thesis/slides` (16 frames) now state the
adjudicated claims: the cost percentages are withdrawn and replaced by the
feasibility result, every gated record is cited, all 21 figures are in.
`docs/THESIS_DRIFT.md` (generated; CI keeps it current) reads 0 bare claims /
0 uncited records / 0 unplaced figures. `research/paper/main.tex` is still the
FGCS skeleton with TODO blocks — carve it from the thesis chapters when a
venue is chosen. Human fill-ins remain: author/degree/university macros in
`thesis/report/main.tex`.

## 8. Submission-adjacent — human

- [ ] arXiv preprint, journal submission, artifact-track application
- [ ] Demo video (optional): kind cluster up → `helm install` → apply
      `tenant-acme.yaml` → load → replicas + cache move together on Grafana
