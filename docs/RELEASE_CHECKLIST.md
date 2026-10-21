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

- 78 records regenerate byte-identically (`scripts/reproduce.py`; CI
  `reproduce` job); 18 more carry a stated reason in `UNGATED`; 56
  pre-registrations; `check_preregs.py` proves no registered text moved after
  its result existed, and `check_prereg_timing.py` judges 51 of 53 campaigns
  ran after their protocol (2 disclosed).
- `research/analysis/RECORDS_INDEX.md` (generated) enumerates all of them;
  `OSF_REGISTRATION.md` carries the 29 OSF rows.
- [ ] The 8 protocols written after the OSF mirror (FAIR_J, STRUCTURAL_MISMATCH,
      RETUNED, WAVE4_REPLICATION, JOINTNESS, ACTUATION, QUALITY, HELDOUT) are
      git-only; the upload package is `Thesis Project/osf_upload_2026-10-16/`.
      Their pushes carry server-side times (GitHub Actions run creation), and
      JOINTNESS/ACTUATION, QUALITY and HELDOUT were archived by Software
      Heritage within seconds of their push (save requests 2560095, 2560208,
      2560399). **Human:** add
      them to the OSF project as a post-run registration, stated as such.
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
      1025 files, adds docs/COMMIT_MAP.md); v3 10.5281/zenodo.23542675
      (2026-10-21, 1082 files: every campaign through QUALITY; HELDOUT ran
      after it — its export is in the repository, its DuckDB goes in v4).
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
- [x] Tag `v1.0.1` (2026-10-16) → `release.yml` passed its govulncheck gate, then
      built, SBOM'd and cosign-signed all four images at `:v1.0.1` and pushed
      both charts at OCI tag `1.0.1` (charts `polyforge` 0.2.1 /
      `polyforge-operator` 0.3.1, appVersion `v1.0.1`). Verified anonymously: all
      four `v1.0.1` manifests answer HTTP 200 and `helm pull
      oci://ghcr.io/mohaimin-8/polyforge/charts/polyforge-operator --version 1.0.1`
      succeeds with no login. v1.0.1 carries the go1.26.9 toolchain and the
      planner/operator fixes of 2026-10-16; **superseded by v1.0.2** (below); never install v1.0.0, whose
      images carry go1.25.14 and known advisories.
- [x] Tag `v1.0.2` (2026-10-22): GO-2026-6809 (`crypto/x509`, reachable) was
      published after v1.0.1 shipped; go1.26.10 fixes it. Release gate, four
      signed images and both charts (OCI `1.0.2`; `polyforge` 0.2.2,
      `polyforge-operator` 0.3.2, appVersion `v1.0.2`) all green. **Install
      v1.0.2.** See `docs/SECURITY.md`.
- [ ] Optional: register the chart on artifacthub.io.

## 6. Live evidence — done

The live sittings that the paper cites ran on rented hosts and are in the
gate as records: the 24 h CRUD-plane soak (`RESULTS_LIVE_SOAK_V8.md`), the
multi-node sitting (`RESULTS_MULTINODE.md`), the trace-driven cluster
(`RESULTS_TRACE_LIVE.md`), and the three-knob live plane B1/B1′/B1″
(`RESULTS_WAVE4_LIVE_PLANE.md`, `RESULTS_WAVE4_CALIBRATED.md`,
`RESULTS_WAVE4_DWELL.md`; runbook `WAVE4_RENTED_HOST_RUNBOOK.md`). Every box
is terminated and the AWS key is deactivated; nothing is running.

## 7. The text — NOT current (revised 2026-10-16)

`thesis/report` and `thesis/slides` were rewritten to the records on
2026-09-16, and the FGCS manuscript (`research/paper/`) was drafted on
2026-09-21. Both predate the 2026-09-26 audit and the campaigns since
(FAIR_J, RETUNED, JOINTNESS, ACTUATION, QUALITY). `docs/THESIS_DRIFT.md`
(generated; CI keeps it current) lists every contradicted claim still made bare,
with file and line — 7 of 12 on 2026-10-16 — and the records no document cites
yet. The text must be rewritten to the records before submission; that is the
author's work. Author fill-ins also remain: author/degree/university macros in
`thesis/report/main.tex`.

## 8. Submission-adjacent — human

- [ ] arXiv preprint, journal submission, artifact-track application
- [ ] Demo video (optional): kind cluster up → `helm install` → apply
      `tenant-acme.yaml` → load → replicas + cache move together on Grafana
