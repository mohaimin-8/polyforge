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
- [x] OSF mirror registered 2026-09-19: **https://doi.org/10.17605/OSF.IO/DYZKV**
      (all 48 protocols + index + commit map, frozen and public; project
      https://osf.io/wt7em/). A post-run witness, stated as such; the git
      push timestamps stay the pre-run anchor.

## 3. Zenodo deposit — done, published 2026-09-17

- [x] `eval/zenodo_creators.json` (author as creator with ORCID
      0009-0003-3254-9532; supervisor as contributor, role Supervisor)
- [x] `cd eval && python scripts/archive_zenodo.py` — 1023 files, 49.8 MB,
      SHA-256 manifest, no placeholder
- [x] Published. **Cite the concept DOI https://doi.org/10.5281/zenodo.22801195**
      (always the latest version). Versions: v1 10.5281/zenodo.22801196
      (2026-09-17, 1023 files); v2 10.5281/zenodo.22807022 (2026-09-17,
      1025 files, adds docs/COMMIT_MAP.md).
      (Islam, M. M. (2026). PolyForge evaluation artifact: pre-registered
      controller-comparison campaigns (harness, baselines, raw results,
      live-run data, IaC) [Dataset]. Zenodo.)
- [x] DOI in `README.md`, `docs/REPRODUCE.md`, the thesis (traceability
      appendix, discussion, `references.bib` entry `polyforge2026zenodo`)
- A new version is needed only if the bundle changes — rebuild, upload as
      *New version*, add the version DOI to the list above; citations do not change.

## 4. Evidence site — LIVE (2026-09-17)

- **https://mohaimin-8.github.io/polyforge/** — every record, pre-registration
  and figure with its caption, rebuilt by `.github/workflows/pages.yml` on
  every push to main (`PAGES_ENABLED=true`, source: GitHub Actions).
- [x] Repository public (2026-09-17); CI green on main, 12/12 jobs.
- [x] `scripts/test_build_pages.py` fails on a gated record without a page, a
      dead link, or two builds that differ.

## 5. Images and charts — public (2026-09-19)

- [x] Tag `v1.0.0` → `release.yml` built, SBOM'd and cosign-signed
      `ghcr.io/mohaimin-8/polyforge/{control-plane,ai-gateway,operator,planner}:v1.0.0`
      and pushed both charts as OCI packages
      `oci://ghcr.io/mohaimin-8/polyforge/charts/{polyforge-operator,polyforge}:1.0.0`
      (the chart's OCI tag is the release tag, not `Chart.yaml`'s `version`).
- [x] All six packages set to Public by the owner (2026-09-19); verified
      anonymously: every tag list answers HTTP 200 and
      `helm pull oci://ghcr.io/mohaimin-8/polyforge/charts/polyforge-operator --version 1.0.0`
      succeeds with no login.
- [ ] Optional: register the chart on artifacthub.io.

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
venue is chosen. Author fill-ins remain: author/degree/university macros in
`thesis/report/main.tex`.

## 8. Submission-adjacent — human

- [ ] arXiv preprint, journal submission, artifact-track application
- [ ] Demo video (optional): kind cluster up → `helm install` → apply
      `tenant-acme.yaml` → load → replicas + cache move together on Grafana
