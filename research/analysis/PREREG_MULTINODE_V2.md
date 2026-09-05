# Pre-registration: node spread, sized to what this host serves (W7, v2)

Frozen and pushed before the campaign runs. The push event is the timestamp
anchor (cardinal rule R1).

`PREREG_MULTINODE.md` is **closed unresolved** — two sittings, both VOID, under
a stopping rule that said two voids close it. This is a **new** campaign, not a
third attempt at that one, and it is designed from what those voids measured
rather than from what they hoped.

## What the closed campaign established

Three facts, all of which constrain this design:

1. **`small` cannot answer this question at all.** `kind_config` builds one
   control-plane plus `nodes - 1` workers, so `small` = 2 nodes = **one
   worker**. Every application pod lands on the same node by construction.
   `medium` (3 workers) is the smallest topology where spread is even possible.
2. **Cluster size does not change demand**, only the replica ceiling
   (`replicas_max` 24 at `small`, 48 at `medium`). Measured offline from
   `workloads.build`: `crud_bursty` peaks at **412.5 rps** and `crud_steady` at
   **235.6 rps**, identically at both sizes.
3. **The killer is the scale-up transient, not the rate.** Sitting 2 achieved
   only 84.7 rps and still failed 10.6% of requests, with `iteration_duration`
   median **9.3 s** and p95 **43 s**, and **6625 dropped iterations** against
   6135 completed. Those are the numbers of a cluster still starting, not of a
   saturated one: the harness gates on `rollout status` for the *initial*
   replicas, then the operator scales toward the ceiling *during* the window,
   and on cold nodes those pods take minutes to start. A 24 h soak absorbs
   that; a 20-minute window is dominated by it.

## Design (frozen)

| | |
|---|---|
| experiment | `eval/experiments/multinode_steady.yaml` |
| cluster size | **`medium`** — 4 nodes = 3 workers (fact 1) |
| workload | **`crud_steady`** — 235.6 rps peak, 43% below `crud_bursty` (fact 2) |
| systems | `jcac` |
| steps | 120 (20 minutes) |
| required env | `POLYFORGE_EVAL_SHARED_PG=1` — multi-replica storage |
| required env | **`POLYFORGE_EVAL_WARMUP_SECONDS=420`** (fact 3) |

**Why 420 s of warm-up, and why that is not weakening a guard.** The warm-up's
own stated purpose is "to have the connection pools, page cache and all sixteen
replicas doing real work before the scored window opens", and **its results are
discarded** — it cannot flatter a measurement because it is not measured. The
default 90 s was set for a warm cluster; sitting 1 observed pods taking roughly
3–4 minutes from namespace creation to `Running` on cold nodes. 420 s covers
that with margin. The scored window's thresholds — zero dropped iterations,
under 1% failures — are **unchanged**, and this campaign is void if they trip.

## Hypotheses (frozen, unchanged from v1)

**MN-H1 (the claim).** At least **2 distinct nodes** appear in `node_shares`,
and the busiest node's share is **< 0.90**. Packing is the scheduler's
business; pinning is the failure this catches.

**MN-H2 (gating).** Every pod in `cpu_ms` resolves to a node — no `"unknown"`
bucket — and the sampler took **≥ 10 samples**. On failure MN-H1 is **VOID,
not FAILED**.

**MN-H3 (descriptive).** If the deployment held one replica throughout, MN-H1
is **VACUOUS**: one pod can only be on one node.

## Stopping rule — stricter than v1

**One sitting. No re-run.** v1 allowed two and used both. If this sitting
voids, **W7 stays open and this campaign closes**, with the void disclosed
here. The question is worth one well-designed attempt, not an open-ended series
against a host that has now twice declined to serve it.

## What a PASS licenses

Exactly: *"the control plane's work was observed on more than one node of a
four-node cluster under steady CRUD load."* It removes **single node** from
W7's residue and leaves **single machine** standing, which §8 already lists as
permanent. It does not license "distributed", "multi-host" or any claim about
node loss — nothing here kills a node.
