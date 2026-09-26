# Experiment cluster IaC (W35a)

Reproducible 4-node k3s cluster on Hetzner Cloud for the W35a smoke runs
and the W35b full matrix. One `apply` gives a working kubeconfig in about
two minutes; `destroy` guarantees the meter stops.

## Usage

```bash
export TF_VAR_hcloud_token=...            # from Hetzner Cloud console
export TF_VAR_admin_cidrs='["203.0.113.7/32"]'  # your IP
terraform init
terraform apply
# fetch kubeconfig (the exact command is printed as an output):
terraform output kubeconfig_hint
```

Then point the harness's cluster backend at it:

```bash
export KUBECONFIG=$PWD/kubeconfig.yaml
python -m harness.runner experiments/smoke.yaml   # backend: cluster
```

## Cost

| item | rate | 7-day W35b estimate |
|---|---|---|
| 4 × cx32 | ~€0.0113/h each | ~€7.60 |
| 6 × cx32 (contingency) | — | ~€11.40 |

Both are far under the $200 experiment budget; the dominant budget risk
is forgetting `terraform destroy`, not node count.

## Notes

- Scale out with `-var worker_count=5` if W35b falls behind schedule.
- The k3s join token is generated per cluster by Terraform and is part of
  the state — treat `terraform.tfstate` as a secret (it is gitignored).
- Firewall admits SSH/6443 only from `admin_cidrs`; node-to-node traffic
  stays on the 10.0.0.0/16 private network (clear of k3s's 10.42/16 pod and
  10.43/16 service ranges).
- Status: written and validated syntactically; **not yet applied** — the
  first `apply` happens when the paid cloud runs are approved (memory:
  cloud spend is a user decision).
