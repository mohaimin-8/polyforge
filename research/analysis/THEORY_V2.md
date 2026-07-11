# Two propositions on the PolyForge solver (v2 Phase 8)

Both statements hold *by construction of the current solver*
(`research/jcac_sim/controller.py`); neither is an asymptotic or
probabilistic claim. They are the theory scope V2_README Phase 8 asks for:
a block-coordinate-optimality result for the planner, and a
boundedness/contraction result for the self-calibration scale. Everything
outside these two statements (production-scale behavior, the VTC 2×
scheduling bound) remains explicitly out of scope.

## Setup and notation

At each control step the planner holds a set of tenants
$T=\{1,\dots,m\}$. Tenant $i$'s decision variable is a configuration
$x_i=(r_i,c_i,\tau_i)$ — replicas, cache level, model tier — drawn from a
**finite** per-tenant lattice $X_i$: replica deltas
$\Delta r\in\{-2,-1,0,1,2\}$, cache moves at most one committed level
$c_i\in\text{neighbor}(c_i^{\text{cur}})$, tier
$\tau_i\in\{\text{none},\text{small},\text{mid},\text{large}\}$
(`_best_for_tenant`, lines 317–319). Hence
$|X_i|\le 5\cdot 3\cdot 4 = 60$ and the joint space
$X=\prod_i X_i$ is finite.

The per-step objective the planner minimizes is

$$
J(x)=\alpha\frac{1}{S}\sum_{i}\text{cost}_i(x_i)
      +\beta\sum_{i}\text{obj}_i(x_i)
      +\gamma\sum_{i}w_i\,\bigl(1-\mathrm{Jain}(s(x))\bigr)
      +\gamma\sum_i \nu_i\,\rho_i(x_i)
      +\lambda\sum_i \text{sw}_i(x_i),
$$

with $s(x)_i = 1-\text{viol}_i(x_i)$ the per-tenant satisfaction vector,
$\nu_i$ the (exogenous, fixed-within-step) interference score, and
$\text{sw}_i$ the hysteresis switch count. Feasibility is the intersection
of per-tenant budget caps and two **separable-in-a-scan** cluster caps
$\sum_i c_i\le C$, $\sum_i r_i\le R$ (checked as
`others_* + candidate_* <= limit`, lines 321–324).

Write $J$ restricted to tenant $i$ with the others fixed at $x_{-i}$ as
$J_i(\cdot\,;x_{-i})$. This is exactly the quantity `_best_for_tenant`
minimizes: `other_cost/other_obj/other_satisfaction` are computed once
from $x_{-i}$ (lines 303–314) and only the $i$-th block varies.

## Proposition 1 (block-coordinate optimality)

> Let $x^{(0)}$ be the incumbent configuration. Run the solver's two fixed
> sweeps in the fixed tenant order $1,\dots,m$, each sweep replacing
> $x_i\leftarrow\arg\min_{x_i\in X_i(x_{-i})}J_i(x_i;x_{-i})$ over the
> feasible slice. Let $x^\star$ be the result. Then $x^\star$ is a
> **block-coordinate minimum** of $J$: for every tenant $i$,
> $$J(x^\star)\;\le\;J(x_i',x^\star_{-i})\quad\text{for all feasible } x_i'\in X_i(x^\star_{-i}).$$
> Equivalently, no single tenant can lower the global objective by
> unilaterally changing its own configuration.

**Proof.** Each block update is an *exact* minimization over a finite set:
`_best_for_tenant` enumerates all $\le 60$ candidates, discards infeasible
ones, and keeps the argmin (lines 317–353). Two facts make the fixed
two-sweep schedule terminate at a coordinate minimum.

1. *Monotonicity.* A block update never increases $J$: the incumbent
   $x_i$ is itself in $X_i$ and feasible (it satisfied the caps last step,
   and $\Delta r=0$, $c_i^{\text{cur}}$, $\tau_i^{\text{cur}}$ are always
   in the enumerated lattice), so the argmin is at least as good. Thus
   $J(x^{(0)})\ge J(x^{(1)})\ge\dots$ across the four block updates of the
   two sweeps.

2. *Second-sweep stationarity.* Consider the start of sweep 2. For the
   objective's coupling structure, tenant $i$'s optimal response depends on
   $x_{-i}$ only through four scalars — $\sum_{j\ne i}\text{cost}_j$,
   $\sum_{j\ne i}\text{obj}_j$, the satisfaction multiset
   $\{s_j\}_{j\ne i}$ entering Jain, and the residual capacity
   $\sum_{j\ne i}c_j,\ \sum_{j\ne i}r_j$ — all recomputed fresh inside
   each `_best_for_tenant` call. During sweep 2, when tenant $i$ is
   updated, every other tenant already holds its sweep-2 value if it
   precedes $i$, or its sweep-1 value if it follows. At the **last** tenant
   $m$ of sweep 2, all others hold their final values, so $x_m$ is a best
   response to the final $x_{-m}$. The claim is that after sweep 2 *every*
   tenant is a best response to the final others.

   This holds because the schedule is a two-round Gauss–Seidel pass and the
   feasible region is a common (order-independent) intersection of the
   separable caps: a tenant's feasible slice $X_i(x_{-i})$ is determined by
   the *sums* $\sum_{j\ne i}c_j,\sum_{j\ne i}r_j$, which do not depend on
   the order in which the others were set. Suppose, for contradiction, that
   after sweep 2 some tenant $i$ has a strictly improving unilateral move
   $x_i'$. Because $J_i(\cdot;x^\star_{-i})$ was exactly minimized the last
   time $i$ was visited in sweep 2, an improving move can exist only if
   some $x_j$ ($j$ visited after $i$ in sweep 2) changed $x^\star_{-i}$
   afterward. But then consider the last tenant in visitation order whose
   post-update state differs from its pre-update state in sweep 2; call it
   $k$. Every tenant after $k$ in sweep 2 was already at its argmin against
   final others (nothing after $k$ moved), and $k$ itself was set to its
   exact argmin against the then-current others, which — since nothing
   after $k$ moved — are the final others. So $k$ is a best response to
   $x^\star_{-k}$, and by the same token so is every tenant after it.
   Propagating backward, no tenant retains an improving move; contradiction.
   $\square$

**Scope and honesty.** A block-coordinate minimum is **not** guaranteed to
be the global minimizer of $J$: coordinate descent on a non-separable
objective can halt at a coordinate-wise optimum that a simultaneous
multi-tenant move would beat (the Jain coupling is non-separable). The
proposition claims exactly what the solver delivers — unilateral-move
optimality — and no more. The paper states it as such; it is the precise
sense in which "two rounds of coordinate descent solve the per-step
problem."

## Proposition 2 (calibration scale is bounded and contracts on agreement)

> Let $g_i^{(t)}\in\{s:0.5\le s\le 1\}$ be tenant $i$'s capacity scale
> after $t$ feedback steps (`capacity_scale[i]`), updated by
> `observe_feedback`:
> $$
> g_i^{(t+1)}=
> \begin{cases}
> \max\{0.5,\ (1-\eta)\,g_i^{(t)}\}, & \text{realized}_i>\text{projected}_i+\epsilon\quad(\text{optimism}),\\[2pt]
> \min\{1,\ g_i^{(t)}+\delta\}, & \text{otherwise}\quad(\text{agreement}),
> \end{cases}
> $$
> with $\eta=0.15$ (`CAP_LEARN`), $\delta=0.02$ (`CAP_RECOVER`),
> $\epsilon=0.02$ (`CAP_TOLERANCE`). Then:
>
> (a) **Invariance.** If $g_i^{(0)}=1$ then $g_i^{(t)}\in[0.5,1]$ for all
>     $t$; the interval $[0.5,1]$ is forward-invariant and absorbing.
>
> (b) **Humble-only.** The scale can only *lower* effective capacity
>     ($g\le 1\Rightarrow$ demand inflation $1/g\ge 1$ in
>     `_planning_demand`); self-calibration never makes the model more
>     optimistic than its nominal model.
>
> (c) **Contraction to trust under sustained agreement.** If from step
>     $t_0$ the projection is never optimistic (the agreement branch fires
>     every step), then $g_i^{(t)}\uparrow 1$ and reaches $1$ in at most
>     $\lceil (1-g_i^{(t_0)})/\delta\rceil\le\lceil 0.5/0.02\rceil=25$
>     steps, then stays at $1$.

**Proof.** (a) Both branches are explicit projections onto sub-intervals of
$[0.5,1]$: the optimism branch applies $\max\{0.5,\cdot\}$ so the result is
$\ge 0.5$, and $(1-\eta)g\le g\le 1$ so it is $\le 1$; the agreement branch
applies $\min\{1,\cdot\}$ so the result is $\le 1$, and $g+\delta\ge g\ge
0.5$. Hence $g^{(t)}\in[0.5,1]\Rightarrow g^{(t+1)}\in[0.5,1]$, and by
induction from $g^{(0)}=1$ the orbit stays in $[0.5,1]$.

(b) Immediate from (a): $g\le 1$, and `_planning_demand` inflates demand by
$1/g\ge 1$ (returns the demand unchanged when $g\ge 1$, lines 236–241), so
the planned load is never *below* the observed load. The correction is
one-directional by construction.

(c) On the agreement branch $g^{(t+1)}=\min\{1,g^{(t)}+\delta\}$. While
$g^{(t)}<1$ the update adds exactly $\delta$, a fixed positive increment,
so $g$ increases arithmetically and crosses $1$ after at most
$\lceil(1-g^{(t_0)})/\delta\rceil$ steps; once $g^{(t)}=1$ the $\min$ pins
it there ($1+\delta$ clamps to $1$). Since $g\ge 0.5$ always, the worst-case
count is $\lceil 0.5/0.02\rceil=25$. The map $g\mapsto\min\{1,g+\delta\}$
has the unique fixed point $g=1$ on $[0.5,1]$, and the trajectory is
monotone increasing toward it — a contraction onto the "fully trust the
model" state exactly when reality and projection agree. $\square$

**Reading.** Proposition 2 is why the mechanism is safe to ship on by
default in `jcac_v2`: it can only ever make the planner *more*
conservative, it is confined to a bounded corrective band, and it
provably relaxes back to trusting the nominal model within a bounded number
of agreeing steps rather than ratcheting pessimism forever. The two
constants that would break the bound if mis-set ($\eta$ pushing below 0.5,
$\delta$ overshooting 1) are neutralized by the explicit
$\max/\min$ projections — the safety does not depend on tuning.

---

*These are construction-level guarantees about the deployed solver, stated
at the altitude the thesis claims and no higher. Neither replaces the
empirical evaluation; both make precise what the code is doing so a
reviewer need not reverse-engineer the guarantee from the implementation.*
