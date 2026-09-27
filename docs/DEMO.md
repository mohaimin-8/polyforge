# Demonstrating PolyForge

How to run PolyForge in front of a supervisor or examiner, what each result
means, and what to say about it. Every command below was run on the author's
Windows 11 laptop on 2026-09-28 and the outputs quoted are the ones it printed.
Commands are PowerShell, run from the repository root unless a step says `cd eval`.

The demonstration has four parts. Parts A, B and D need only Go and Python and
take about 25 minutes together. Part C (a live Kubernetes cluster) needs Docker
and adds about 15 minutes; it is optional.

| part | what it proves | time | needs |
|---|---|---|---|
| A. The platform runs | the services are real, multi-tenant and secure | 8 min | Go, Python |
| B. The controller and the result | what joint control does and what the research found | 10 min | Python |
| C. Kubernetes, live (optional) | the operator and planner drive a real cluster | 15 min | Docker, kind, kubectl, helm, k6 |
| D. The evidence is real | tests pass and every record regenerates byte for byte | 5 min | Go, Python |

---

## 0. The day before: rehearse (about 30 minutes)

Run each of these once. If all of them succeed, the demonstration will work.

```powershell
# 1. The platform demo checks its own results; it must end "All 13 checks passed."
python scripts/demo_platform.py

# 2. The controller comparison (about 10 seconds)
cd eval
python scripts/demo_compare.py --sweep
python scripts/demo_compare.py --workload agentic --plot agentic_trace.png
cd ..

# 3. The evidence site, built locally (under a second); open tmp/site/index.html
python scripts/build_pages.py --out tmp/site

# 4. Only for Part C, with Docker Desktop running: rebuild the three images
#    so the cluster runs today's code (about 8 minutes the first time, while it
#    downloads the pinned base images; much faster after that)
docker build -t polyforge/control-plane:dev .
docker build -f Dockerfile.operator -t polyforge/operator:dev .
docker build -f services/planner/Dockerfile -t polyforge/planner:dev .
```

On the day, close anything that uses ports 8080, 8081, 11501, 11502 or 11503
(for example a control plane started with `scripts/run-control-plane.ps1`).
`demo_platform.py` refuses to start and names the busy port if you forget.

Open three things before the supervisor arrives: one terminal at the repository
root, the file `agentic_trace.png` from step 2, and `tmp/site/index.html`.

---

## The story in one minute (say this first)

> Cloud platforms now host ordinary web traffic and AI traffic to large
> language models side by side, for many customers at once. Each customer
> ("tenant") needs three resources decided every few seconds: how many
> servers, how much semantic cache (answers to repeated questions), and which
> size of language model. Industry controls each of these separately. My
> research question is whether deciding all three **together**, for all
> tenants, against one objective of cost, SLO violation and fairness, does
> better. I built the platform, the controller (JCAC), a simulator and an
> evaluation harness, and I tested it with pre-registered experiments.
>
> The honest answer: joint control clearly beats controlling the three
> knobs separately (53-60% better on the objective). Against a fairly tuned
> standard autoscaler it ties. On real GPU hardware it had to calibrate its
> model online before it won.

---

## Part A. The platform runs (about 8 minutes)

```powershell
python scripts/demo_platform.py --pause --keep-running
```

The script builds the control plane and the AI gateway (Go), starts them on
this laptop with three mock language models, and walks through seven acts. It
waits for Enter between acts so you can explain each one. Every act prints
`[PASS]` or `[FAIL]` for what it checked.

**Why mock models?** A real model needs a GPU. The mocks answer after 100 ms
(small), 300 ms (mid) and 800 ms (large), standing in for the 0.5B, 3B and 7B
Qwen models used on the rented GPU server. Everything else (authentication,
cache, knobs, classifier, database) is the real code.

| act | what you will see | what to say |
|---|---|---|
| 1. Two customers sign up | two tenants created, each with a one-time API key | "Each tenant has its own identity and keys; the key is shown once." |
| 2. Tenant isolation | Acme's key reading Globex's projects gets **HTTP 401** | "One tenant can never read another tenant's data. In production this is enforced a second time by PostgreSQL row-level security." |
| 3. The semantic cache | first question ~320 ms (**miss**, it paid for a model call); the same question again ~20 ms (**hit**); a near-duplicate is also a hit (similarity 0.97) | "Repeated questions are answered from cache, which skips the expensive model. That is the cache knob." |
| 4. Per-tenant cache | Globex asks Acme's exact question and gets a **miss** | "The cache is split per tenant. If it were shared, a fast answer would tell Globex that Acme asked that question. I measured this attack over the network: a shared cache leaks completely (AUC 1.000); the per-tenant cache leaves the attacker at chance (AUC 0.502)." |
| 5. The model-tier knob | the admin API switches Acme's tier; answers take ~100, ~300, ~800 ms | "This is the API the Kubernetes operator calls every 10 seconds to apply the controller's plan. The tier knob works live." |
| 6. The workload classifier | after traffic, Acme is labelled **AI-cacheable**, Globex **CRUD-steady**, each with a resource recommendation | "The platform watches each tenant's traffic and classifies it; the controller plans per class." |
| 7. Observability | ~200 Prometheus metric series; request ID and trace header on every response | "Everything is measurable; the experiments read these metrics." |

At the end the services keep running. Open
`http://localhost:8080/admin/workloads` in a browser, enter the admin key
`pf_admin_demo`, and show the live classifier dashboard. Press Enter in the
terminal to stop everything.

**Be ready for:** "Is that real semantic matching?" The demo's embedder is a
small offline one, so it only matches near-duplicates (case and punctuation).
The research measured the cache with a real sentence encoder (MiniLM) on
200,000 real user turns from LMSYS-Chat-1M: 29.6% hit rate at similarity 0.85
(`research/analysis/SEMANTIC_CACHE.md`).

---

## Part B. The controller and the result (about 10 minutes)

```powershell
cd eval
python scripts/demo_compare.py
```

This runs one simulated scenario (8 tenants, AI traffic, 120 control
intervals of 10 seconds = 20 simulated minutes) under four controllers, live,
through the exact code the pre-registered experiments used. It takes about
two seconds. Output on 2026-09-28:

```
                                    HPA-fair (tuned)     KEDA-fair (tuned)  Layered 3-knob stack      PolyForge (JCAC)
J (scored; lower is better)                    0.319           0.324 (+2%)         1.122 (+252%)          0.361 (+13%)
cost for the window                            $2.97           $3.08 (+4%)        $10.62 (+258%)          $3.41 (+15%)
mean SLO violation                             0.004        0.000 (-88% *)        0.003 (-18% *)        0.001 (-62% *)
tenant-steps violating                          6.0%         1.5% (-76% *)         5.4% (-10% *)         0.8% (-86% *)
Jain fairness index                            1.000           1.000 (+0%)           1.000 (+0%)           1.000 (+0%)
AI cache hit rate                              16.3%           16.3% (+0%)        19.5% (+19% *)          13.2% (-19%)
tenant-steps shedding AI                        0.0%                  0.0%                  0.0%                  0.1%
```

**The four controllers.**
- *HPA-fair*: Kubernetes' standard autoscaler, which moves only the server
  count, given a well-sized cache and tuned on the same objective.
- *KEDA-fair*: the event-driven autoscaler, set up the same way.
- *Layered 3-knob stack*: holds all three knobs but runs three separate rules,
  one per knob. This is the fair test of jointness: same knobs, no coordination.
- *PolyForge (JCAC)*: model-predictive control. Every 10 seconds it forecasts
  each tenant's demand, tries candidate moves of all three knobs for every
  tenant, predicts cost, latency and fairness with a system model, and picks
  the combination that minimises the objective within the cluster's limits and
  each tenant's budget.

**How to read the table.** Percentages are against the first column. A `*`
means the change is in the good direction. **J** is the single number the
research is scored on:

> J = normalised cost + 2 x SLO violation + 0.5 x (1 - Jain fairness)

Lower is better. Every baseline was tuned to minimise this same J, so the
comparison is on their terms.

**What to say about this scenario.** "Here PolyForge buys more servers than
HPA. It violates the SLO on 0.8% of tenant-steps instead of 6.0%, but that
costs 15% more, and J rates the trade in HPA's favour, 13% worse overall.
Against the layered stack, which has the same knobs without coordination,
PolyForge is 68% better. One scenario is an illustration, not evidence."

### B2. Watching the knobs move

```powershell
python scripts/demo_compare.py --workload agentic --plot agentic_trace.png
```

Open `agentic_trace.png`. It has five panels over the same 20 minutes: the
offered demand, then each controller's **servers**, **cache**, **model tier**
and **cost per step**.

- HPA and KEDA move only the server line; their cache and tier lines are flat.
- The layered stack moves all three, but each rule acts alone: its tier rule
  upgrades to the mid model and its cost line sits far above everyone else's.
  That is the price of three knobs without coordination.
- PolyForge fills its server allowance early, grows the cache, and during
  demand peaks drops some tenants' AI requests below the small model (the tier
  line dips toward "none"). That is **shedding**, a designed outage for
  tenants at their budget limit, and it counts as SLO violation in J. Say so
  before you are asked: in this scenario it sheds AI on 10.7% of tenant-steps.

In this agentic scenario PolyForge has the lowest J (0.721, against 0.850 for
HPA-fair).

### B3. Where it wins and where it does not

```powershell
python scripts/demo_compare.py --sweep
```

About 10 seconds; one row per traffic class, `<` marks the lowest J:

```
workload (J, lower is better)       HPA-fair (tuned)     KEDA-fair (tuned)  Layered 3-knob stack      PolyForge (JCAC)
crud_steady                                  0.028                 0.134                 0.027 <               0.027
crud_bursty                                  0.136                 0.091                 0.136                 0.075 <
ai_cacheable                                 0.533                 0.579                 0.490 <               0.541
ai_uncacheable                               0.319 <               0.324                 1.122                 0.361
agentic                                      0.850                 0.911                 3.120                 0.721 <
```

"PolyForge wins on bursty and agent traffic, where planning ahead pays. It
loses on cacheable AI traffic, where the separate rules are already good. No
controller wins everywhere. That is why the real test is the pre-registered
one."

### B4. The pre-registered result (the part that counts)

Open `tmp/site/index.html` and click `RESULTS_FAIR_J`, or read
`research/analysis/RESULTS_FAIR_J.md`. The protocol was pushed to GitHub
**before** the 2,100 runs: 60 design points (5 traffic classes x 4 tenant mixes
x 3 cluster sizes) x 5 seeds, all arms seeing identical demand.

| test | against | J difference | verdict | meaning |
|---|---|---|---|---|
| FJ-H1 | HPA-fair | -0.0037 (-0.9%), CI [-0.023, +0.017] | FAIL | a tie |
| FJ-H2 | KEDA-fair | -0.0305 (-7%), CI [-0.054, -0.007] | PASS | better |
| FJ-H3 | layered 3-knob stack | -0.611 (-60%), CI [-0.865, -0.380] | PASS | much better |

With HPA and KEDA re-tuned for every cluster size (`RESULTS_RETUNED.md`,
1,800 runs), HPA ties exactly (RT-H1 FAIL), KEDA is no longer beaten (RT-H2
FAIL), and the layered-stack win holds at -53% (RT-H3 PASS). The re-tuned
autoscalers halve SLO overshoot but spend about 24% more.

**Say it plainly:** "My original claim was that PolyForge beats every
baseline. An audit I ran on my own work found the baselines had been
handicapped. Against fair baselines the win over standard autoscaling
disappears; the win over uncoordinated control does not. I report the tie."

### B5. Optional: what the audit changed

```powershell
python scripts/demo_compare.py --published
```

This reruns the pre-audit comparison: the published HPA paid an extra cache
charge and kept a small fixed cache, and against it PolyForge looked 25%
cheaper. The script prints a warning that this cast is the handicapped one.
Showing both versions, handicapped and fair, is a strong way to show that the
research is careful.

---

## Part C. Kubernetes, live (optional, about 15 minutes)

Docker Desktop must be running and the images rebuilt (step 0.4). Start this
at the **beginning** of the meeting in a second terminal. It needs about
about 4 minutes to create the cluster and install everything, then applies
5 minutes of load; the whole run takes about 10 minutes:

```powershell
Remove-Item eval/results/demo_cluster* -Recurse -Force -ErrorAction SilentlyContinue
$env:POLYFORGE_EVAL_SHARED_PG = "1"      # one Postgres for all 16 control-plane replicas
$env:POLYFORGE_EVAL_KEEP_CLUSTER = "1"   # leave the cluster up afterwards
cd eval
python -m harness.runner experiments/demo_cluster.yaml --workers 1
```

It creates a kind cluster, installs PolyForge with Helm, installs the operator
and the planner, creates one Tenant, Policy and Budget resource per tenant,
waits until the operator reports every Policy **Applied** (the proof that the
operator really scaled the Deployment), then drives traffic with k6.
`demo_cluster.yaml` writes only to `eval/results/demo_cluster*`, which is
gitignored and can never mix with the recorded evidence.

While the load runs, in the first terminal:

```powershell
kubectl get tenants,policies,budgets -A          # PolyForge's own resource types
kubectl get deployments -A                        # the replica counts the operator sets
kubectl get pods -A                               # control plane, operator, planner, NATS
kubectl get policies -A -w                       # live: the SOURCE column turns to 'planner'
```

What you will see (measured on 2026-09-28): about 4 minutes in, the Policy
`SOURCE` column changes from `manual` to `planner`. From then on the JCAC
planner owns every tenant's replicas, cache and tier, and each decision is
audited (248 plan records in this run). The control plane runs as 16 pods on
one shared PostgreSQL, and k6 sends about 29,000 requests in 5 minutes. At this
light load the planner holds every tenant at 2 replicas, 128 MB and the small
tier for the whole window, so the columns do not move; this part shows the
control loop working, not a controller win. When the runner prints
`"ok": true`, show the measured result:

```powershell
Get-Content ../eval/results/demo_cluster_evidence/eval-export.json -TotalCount 12
```

On 2026-09-28: cost $1.77 for the window, zero SLO violations, Jain 1.000,
p95 latency 1.0 ms for CRUD and 20 ms for AI.

**Known issue, found while preparing this demo (2026-09-28).** The planner's
demand signal is a request rate counted inside each control-plane pod
(`internal/telemetry/rate.go`), and the feature API averages those per-pod
rates. With 16 pods behind a load-balancing Service, the planner saw roughly
one twelfth of the real CRUD demand in this run (tenant t00: 4.1 chat
requests/s sent, 0.36 reported). That is a defect in the live demand signal
(`research/analysis/RESULTS_MASTER.md`, finding of 2026-09-28). An opt-in fix
reads the rate from the shared store instead; add
`$env:POLYFORGE_EVAL_DEMAND_RATE = "count"` before the run to use it, and the
planner visibly grows the tenants' caches (128 MB to 256-512 MB) during the
load. If you show Part C, say which signal you ran.

Say: "The same controller code that ran in the simulator is now a service in
the cluster. Every 10 seconds the operator asks the planner for a plan and
applies it to real Deployments, the cache budget and the model tier. On a
rented GPU server with three real models (Qwen 0.5B, 3B and 7B), the first
live run lost: its model of the servers was about 1,000 times too
pessimistic. With online calibration it won in the primary cell (B1',
`RESULTS_WAVE4_CALIBRATED.md`). A five-seed replication is pre-registered and
waits for cloud access."

With `POLYFORGE_EVAL_KEEP_CLUSTER=1` the cluster stays up after the run. Remove
it afterwards:

```powershell
kind delete cluster --name polyforge-eval
Remove-Item Env:POLYFORGE_EVAL_KEEP_CLUSTER, Env:POLYFORGE_EVAL_SHARED_PG
```

---

## Part D. The evidence is real (about 5 minutes)

```powershell
go test ./...                               # all Go packages, about 40 seconds
python -m pytest research/jcac_sim -q       # the controller and simulator, 204 tests, seconds
python scripts/reproduce.py                 # about 20 minutes: start it first
```

`reproduce.py` regenerates every gated record and figure from the raw data
into `reproduce_out/` and compares them with the committed ones byte for byte.
If one number in a published record could not be regenerated, this would fail.
It takes about 20 minutes, so start it in its own terminal at the beginning of
the meeting (or the day before) and show its last lines. On 2026-09-28 they read:

```
  records          72/72 identical -- 56 byte-for-byte, 16 differing only in line endings
  record coverage  72 gated, 18 outside the gate with a stated reason, 0 unaccounted for
  figures          21/21 rebuilt as vector PDF + 600-DPI PNG
  figure files     42/42 byte-identical -- same renderer: win32, matplotlib 3.10.0
committed records and figures were not modified.
```

Then show how pre-registration works, in git:

```powershell
git log --format="%h %ad %s" --date=iso -- research/analysis/PREREG_FAIR_J.md
```

"The protocol, including the scoring script, was pushed to GitHub before the
first run. `scripts/check_prereg_timing.py` checks this for every campaign in
CI: 46 of 48 ran after their protocol, and the two that did not are named in
the script and the records."

In `tmp/site/index.html` show `HYPOTHESIS_LEDGER`: all 96 registered tests
with a p-value; 60 passed as registered, 51 still pass after a correction
across the whole programme. The failures are published too.

---

## Numbers to have ready

| claim | number | record |
|---|---|---|
| J vs fair HPA | tie: -0.9%, CI includes 0 | `RESULTS_FAIR_J.md` (FJ-H1) |
| J vs fair KEDA | -7% | `RESULTS_FAIR_J.md` (FJ-H2) |
| J vs layered 3-knob stack | -60% (FAIR_J), -53% (RETUNED) | FJ-H3, RT-H3 |
| AI traffic shed by PolyForge | 7.2% of tenant-steps (the others 0%) | `RESULTS_FAIR_J.md` |
| cache side channel over the wire | shared AUC 1.000; per-tenant 0.502 | `eval/results/security/RESULTS_WIRE_ATTACK.md` |
| real-data cache hit rate | 29.6% at similarity 0.85 (LMSYS, 200k turns) | `SEMANTIC_CACHE.md` |
| live GPU, first run (B1) | joint controller +23.3% cost vs its tier-only ablation: FAIL | `RESULTS_WAVE4_LIVE_PLANE.md` |
| live GPU, calibrated (B1') | cheapest of 4 comparators at equal fairness: PASS | `RESULTS_WAVE4_CALIBRATED.md` |
| scaling | 1,024 tenants: one plan 71 s; 32-tenant cells 195 ms each | `PLANNER_CELLS.md` |
| 24-hour live soak | 25,806,353 requests, 0 dropped, 0 restarts | `RESULTS_LIVE_SOAK_V8.md` |
| registered tests | 96 with p; 60 PASS; 51 survive programme-wide Holm | `HYPOTHESIS_LEDGER.md` |

## Questions you may be asked

**"Is this only a simulation?"** Mostly, and I say so. The platform is real
software (Part A), the controller has run live on a kind cluster and on a
rented GPU server, and a 24-hour live soak served 25.8 million requests. The
controller comparisons at scale are in the simulator, and the structural-mismatch
campaign measures what happens when the simulator's model is wrong
(`RESULTS_STRUCTURAL_MISMATCH.md`).

**"Why doesn't it beat the standard autoscaler?"** Once the autoscaler gets a
properly sized cache and is tuned on the same objective, a reactive rule is
already close to optimal on most traffic. Joint control pays off where the
knobs interact, which is exactly the comparison with the layered stack.

**"Why these weights in J?"** They were fixed before the evaluation.
`SENSITIVITY_J.md` re-scores the results under other weights and
`OBJECTIVE_FORM.md` under other forms of the objective. Both were run against
the pre-audit comparators, so they show how the old claim depended on the
weights, not how the fair result does; say that if you cite them.

**"Why does it shed traffic?"** Each tenant has a budget. When the plan that
meets the SLO would exceed the budget, the controller drops AI requests
instead of overspending. That costs SLO violation, which J charges.

**"What is new here?"** Controlling replicas, semantic cache and model tier
together across tenants, with a measured answer to when that helps; the
cross-tenant cache side channel and its defence, measured over the wire; and
a fully pre-registered, reproducible evaluation that reports its own failures.

## If something goes wrong

| symptom | fix |
|---|---|
| `Ports [8080] are already in use` | stop the other control plane (or a previous demo) and rerun |
| `go: command not found` | install Go 1.25, or run `python scripts/demo_platform.py --no-build` if `tmp/demo/*.exe` already exist |
| act 6 shows `none yet` | the classifier relabels every 10 s; rerun the script (it waits up to 45 s) |
| `failed to connect to the docker API` | start Docker Desktop and wait until it says "Engine running" |
| cluster run ends at once with "valid" | its result file already has the run: delete `eval/results/demo_cluster*` and rerun |
| a `demo_compare.py` number differs from this page | the code changed after 2026-09-28; the records, not this page, are the reference |
