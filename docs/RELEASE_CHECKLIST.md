# W39/W35c release checklist — human-action items

Everything below needs an account, a camera, or a payment method, so it
cannot be automated from this repo. The automatable half is already done
(charts, docs, templates, archive bundle); each item lists exactly what to
do with it.

## 1. Zenodo deposit (W35c — required for FGCS reproducibility)

- [ ] Run `cd eval && python scripts/archive_zenodo.py` (bundle + manifest + `deposit.json` land in `eval/results/zenodo/`)
- [ ] zenodo.org → New upload → attach the zip
- [ ] Paste metadata from `deposit.json`; fill in your real name in `creators`
- [ ] Publish → copy the DOI into the paper's Reproducibility section

## 2. Publish the Helm chart to Artifact Hub (W39)

- [ ] `helm package deploy/helm/polyforge-operator` (on a machine with helm)
- [ ] Host the chart repo: easiest is a `gh-pages` branch + `helm repo index . --url https://<user>.github.io/polyforge`
- [ ] artifacthub.io → Control panel → Add repository (kind: Helm) → point at the pages URL
- [ ] Verify `helm install polyforge-operator polyforge/polyforge-operator` works from a clean machine

## 3. Container images

- [ ] Build and push `ghcr.io/<user>/polyforge-operator` and `ghcr.io/<user>/polyforge-planner`
      (release.yml already signs images with cosign; align tags with the chart's `appVersion`)
- [ ] Update `deploy/helm/polyforge-operator/values.yaml` repositories if the GHCR org differs

## 4. Demo video (W39)

- [ ] Record ~5 min with OBS: kind cluster up → `helm install` → apply `tenant-acme.yaml` → generate load (`cmd/loadgen`) → watch JCAC adapt on Grafana
- [ ] Upload to YouTube (unlisted is fine initially), embed link + a gif in README
- [ ] Suggested beats: 30 s problem statement → install → burst hits, replicas + cache move together → cost panel drops vs HPA

## 5. Paper-supplement website (W39)

- [ ] GitHub Pages from `docs/` or a `gh-pages` branch
- [ ] Include: figure gallery (`eval/results/figures/`), FIGURES.md captions, link to Zenodo DOI and the repo

## 6. First cloud smoke (W35a→b, ~€10 total)

- [ ] `cd eval/infra/terraform && terraform apply` (needs `TF_VAR_hcloud_token`, your IP in `admin_cidrs`)
- [ ] Implement `control-plane eval-export` (contract in `eval/README.md`) and wire the chart values listed there
- [ ] Run `experiments/smoke.yaml` with `backend: cluster`; append failures to `eval/SMOKE_BUGS.md`
- [ ] `terraform destroy` — the budget risk is forgetting this line

## 7. Submission-adjacent (M10, not code)

- [ ] arXiv preprint, FGCS Editorial Manager submission, SoCC/Middleware artifact-track application — see `docs/THESIS_DETAILS.html` W37–W40
