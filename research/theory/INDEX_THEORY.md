# The interaction index: definitions, statements, proofs

Programme 2, work package WP3. **Status: drafted 12 November 2026 and
property-tested (`test_interaction_index.py`); not yet read by a second
person. Until it has been, call these statements "property-tested", not
"proved".** The executable form is `interaction_index.py`; the planner
adapter is `controller_index.py`.

## Setting

One decision with knobs k = 1, ..., K. Knob k takes a value in a finite list
V_k; the box is B = V_1 × ... × V_K. The objective φ maps a point of B to a
real number, or to ⊥ when the point is infeasible. The anchor x⁰ ∈ B is the
state the decision starts from. Write (x⁰₋ₖ, v) for x⁰ with knob k set to v.

- **Main effect**: fₖ(v) = φ(x⁰₋ₖ, v) − φ(x⁰).
- **Remainder**: R(x) = φ(x) − φ(x⁰) − Σₖ fₖ(xₖ), defined when φ(x), φ(x⁰)
  and every φ(x⁰₋ₖ, xₖ) are feasible. R is zero on every axis through x⁰.
- **Per-knob choice** x^D: for each k, a minimiser of fₖ over the values with
  a feasible axis point (the first in list order on ties; a knob with no
  feasible axis value holds x⁰ₖ). This is what K independent one-knob
  searches from x⁰ produce.
- **Joint choice** x^J: a minimiser of φ over B.
- **Interaction index** I = max R − min R over the points of B where R is
  defined. Because R(x⁰) = 0, I ≥ 0.
- **Quantile index**: with n points where R is defined and m = ⌈0.05 n⌉, the
  m-th largest minus the m-th smallest value of R; it is at most I.

## Theorem 1 (per-knob search)

If φ(x⁰), φ(x^D), φ(x^J) are feasible and R(x^D), R(x^J) are defined, then

    0 ≤ φ(x^D) − φ(x^J) ≤ R(x^D) − R(x^J) ≤ I.

*Proof.* The first inequality holds because x^J minimises φ over B and
x^D ∈ B. For any x where R is defined, φ(x) = φ(x⁰) + Σₖ fₖ(xₖ) + R(x).
Since R(x^J) is defined, every axis point (x⁰₋ₖ, x^J_k) is feasible, so x^J_k
is among the values x^D_k was chosen from and fₖ(x^D_k) ≤ fₖ(x^J_k). Then

    φ(x^D) − φ(x^J) = Σₖ [fₖ(x^D_k) − fₖ(x^J_k)] + R(x^D) − R(x^J) ≤ R(x^D) − R(x^J).

Both remainders are values of R on B, so their difference is at most I. ∎

**Scaling (two knobs).** For K = 2, R(x) = φ(x₁, x₂) − φ(x₁, x⁰₂) −
φ(x⁰₁, x₂) + φ(x⁰), the mixed second difference over the rectangle spanned
by x⁰ and x. If |R(x)| ≤ M · |x₁ − x⁰₁| · |x₂ − x⁰₂| (lattice steps) for all
x ∈ B, then I ≤ 2 M δ₁ δ₂, with δₖ the largest move of knob k in B. With
more knobs, R is the sum of the pairwise and higher-order terms of the
anchored decomposition, each bounded the same way. The loss of per-knob
search therefore shrinks with the product of the step sizes. This is a
consequence of the stated assumption on M, not a claim that M is small; M
is measured, not assumed.

## Theorem 2 (iterated one-knob search)

Let x^C be coordinate-wise minimal: φ(x^C) is feasible and no change of a
single knob lowers φ. Let R^C be the remainder anchored at x^C. If φ(x^J) is
feasible and R^C(x^J) is defined, then

    0 ≤ φ(x^C) − φ(x^J) ≤ −R^C(x^J) ≤ max R^C − min R^C.

*Proof.* Anchored at x^C, every main effect f^C_k(v) = φ(x^C₋ₖ, v) − φ(x^C)
is ≥ 0 where feasible, by coordinate-wise minimality. Hence
φ(x^J) = φ(x^C) + Σₖ f^C_k(x^J_k) + R^C(x^J) ≥ φ(x^C) + R^C(x^J), which
rearranges to the second inequality. R^C(x^C) = 0 gives the third. ∎

## Proposition T3 (the bound is tight)

For every ρ > 0 and 0 < η < ρ there is an instance with I = ρ and
φ(x^D) − φ(x^J) = ρ − η. So no bound c · I with c < 1 holds for every
objective.

*Instance.* Two knobs with values {0, 1}, x⁰ = (0, 0), φ(0, 0) = 0,
φ(1, 0) = φ(0, 1) = −η, φ(1, 1) = ρ − 2η.
*Proof.* f₁(1) = f₂(1) = −η < 0, so x^D = (1, 1) and φ(x^D) = ρ − 2η. Because
ρ > η, ρ − 2η > −η, so φ(x^J) = −η and the loss is ρ − η. R(1, 1) =
ρ − 2η + 2η = ρ and R = 0 elsewhere, so I = ρ. ∎

## Proposition T5 (estimating the index by sampling)

Draw m points uniformly, with replacement, from the n points where R is
defined. Then

    P(range of the m sampled remainders ≥ quantile index) ≥ 1 − 2 (0.95)^m,

and the sampled range never exceeds I. For m = 59 the probability is at
least 0.903.

*Proof.* At least a 0.05 share of the points have R ≥ q_hi (the m-th
largest), so the chance that no draw reaches q_hi is at most 0.95^m;
likewise for q_lo. If both tails are reached, the sampled range is at least
q_hi − q_lo. Apply the union bound. ∎

Cost on a running system: m evaluations of φ, plus the anchor and one
evaluation per value of each knob for the axes.

## What the statements do not cover

- **Constraints.** Budgets and cluster caps make points infeasible. The
  theorems speak about a decision only when their hypotheses hold. The code
  reports the share of decisions where the combination of per-knob winners
  is itself infeasible and must be repaired ("constraint coupling"); that
  share is outside the bound and is reported separately.
- **Whole runs.** Both theorems are about one decision with everything else
  fixed. The per-knob and joint trajectories separate after the first
  disagreement, so neither bounds the difference between two runs. A
  run-level statement is plan item T4 (WP18) and is not claimed here.
- **Novelty of the mathematics.** The statements are elementary. Their use
  is as an instrument: a quantity that any planner which scores a box of
  candidates already computes, and whose size the experiments test as a
  predictor.

## Additions of 13 November 2026: property checks (WP3), T6 and T4 (WP18)

**Status.** Everything in this and the following sections is a draft that
no second person has read. "Checked" means checked numerically by the
files named; it does not mean proved.

**Property checks.** `property_check.py` ran Theorems 1 and 2 on 62,500
random boxes (the plan asked for 100,000; five chunks were run) (2-4 knobs, 2-17 values each; additive objectives with
interaction of controlled size, adversarial, tight and infeasible cases in
14 families), T3 on 100,000 random (ρ, η) and T5 exactly on every box. No
statement failed, and the library agreed with an independent numpy oracle
on every case. Record: `PROPERTY_CHECKS.md`. Two facts the checks add:

- *Theorems 1 and 2 are attained, not only approached.* In T3 with η = 0
  and each knob's improving value listed first, the per-knob tie-break
  takes (1, 1), and φ(x^D) − φ(x^J) = R(x^D) − R(x^J) = I = ρ. For
  Theorem 2: let x^C have φ(x^C) = 0, two single-knob moves with value 0
  (ties, so x^C is coordinate-wise minimal), and φ = −ρ where both moves
  are made together. Then φ(x^C) − φ(x^J) = −R^C(x^J) = max R^C − min R^C = ρ.
- *Theorem 1's hypothesis on R(x^J) cannot be dropped.* Two knobs {0, 1},
  anchor (0, 0), φ(0, 0) = 0, φ(1, 0) = −1, φ(0, 1) infeasible,
  φ(1, 1) = −5: x^D = (1, 0) and x^J = (1, 1) are feasible, R(x^J) is
  undefined, the loss is 4 and the index 0. In the random boxes the loss
  exceeded the index in 2,670 of the 4,025 cases that failed only this hypothesis.

*Notation in T5.* In T5 and its proof, m is the number of draws. The rank
in the definition of the quantile index is a different number,
j = ⌈0.05 n⌉ (written m in the Setting section); "q_hi (the m-th largest)"
in the proof of T5 means the j-th largest.

## Proposition T6 (knobs as players of a game), draft

*Setting.* The box B and an objective φ: B → ℝ with every point feasible
(infeasible points below). G_φ is the game whose players are the knobs:
player k chooses x_k ∈ V_k, and every player's cost is φ(x).

*Statement.*

1. φ is an exact potential of G_φ in the sense of Monderer and Shapley
   (1996): when player k alone moves from x to (x₋ₖ, v), its cost changes
   by exactly φ(x₋ₖ, v) − φ(x).
2. The pure Nash equilibria of G_φ are exactly the coordinate-wise minima
   of φ, the points x^C of Theorem 2. Every joint minimiser x^J is one.
3. Iterated one-knob search (`coordinatewise_minimum`) is round-robin
   best-response dynamics in G_φ; it stops after finitely many moves, at
   an equilibrium. The code also caps the rounds at 100 and raises beyond
   that; the cap was never reached in the 100,000 checks.
4. Theorem 2 therefore bounds how far any equilibrium x^E can be from the
   optimum, by the remainder anchored at that equilibrium:
   0 ≤ φ(x^E) − φ(x^J) ≤ −R^E(x^J) ≤ max R^E − min R^E. In game terms the
   best equilibrium is optimal, and the worst loses at most the largest
   index anchored at an equilibrium.

*Proof (draft).* (1) Every player's cost is φ, so the identity holds term
by term. (2) x is an equilibrium iff no player lowers its cost by a
unilateral move, i.e. iff no single-knob change lowers φ, which defines a
coordinate-wise minimum; a joint minimiser admits no lower value at all.
(3) Each accepted move strictly lowers φ and B is finite, so the search
stops, and it stops only where no single-knob move lowers φ. (4) Theorem 2
with x^C = x^E. ∎

The proof is one line per item; the content of T6 is the reading. In team
decision theory a coordinate-wise minimum is a person-by-person optimal
decision. Radner (1962) showed that for static teams whose cost is convex
and continuously differentiable (with further conditions), person-by-person
optimal decisions are team optimal. A finite box offers no convexity to
appeal to, and Theorem 2 measures the gap with the remainder instead.

*Infeasible points.* Replace ⊥ by a constant M above every feasible value.
The penalised objective is an exact potential. Its equilibria are the
feasible coordinate-wise minima of φ, plus the infeasible points from which
no single-knob move reaches a feasible one. `coordinatewise_minimum` can
stop at such a point, and `theorem2_terms` then returns None.

*What does not follow.*

- **Per-layer controllers with their own objectives do not play G_φ.** If
  controller k minimises a local objective ψ_k, the game G_ψ has an exact
  potential iff, around every closed path of unilateral moves, the movers'
  cost changes sum to zero, and paths of four moves suffice (Monderer and
  Shapley 1996). These are linear equations in the ψ_k, and local
  objectives chosen independently satisfy them only by accident.
- **Aligned objectives are exactly the case where φ is the potential
  (draft).** G_ψ has φ as an exact potential iff ψ_k(x) = φ(x) + D_k(x₋ₖ)
  for every k, where D_k does not depend on knob k. (If: the D_k cancel in
  every unilateral difference. Only if: D_k = ψ_k − φ has zero unilateral
  differences in x_k.) Then G_ψ has the equilibria of G_φ, and Theorem 2
  applies. An aligned controller may add any cost it cannot influence, and
  nothing else.
- **Counterexample 1: no equilibrium.** Two knobs with values {0, 1};
  ψ₁(x) = 1 if x₁ ≠ x₂ else 0 (the first controller follows the second),
  ψ₂(x) = 1 if x₁ = x₂ else 0 (the second leans against the first). No
  point is an equilibrium, and best responses cycle (0,0) → (0,1) → (1,1) →
  (1,0) → (0,0). Around that cycle the movers' changes sum to −4, so no
  exact potential exists, and no property of φ, whatever φ is, says where
  these controllers end.
- **Counterexample 2: a potential game with the wrong potential, at index
  zero.** φ(x) = −(x₁ + x₂) on {0, 1}² is additive: R ≡ 0, the index is 0
  at every anchor, and Theorem 2 says every coordinate-wise minimum of φ is
  optimal. Controllers that each minimise their own resource,
  ψ_k(x) = x_k, play a potential game (potential x₁ + x₂). Its only
  equilibrium is (0, 0), which loses 2 on φ. The index measures
  interaction; it cannot see misalignment.
- Outside the aligned case, bounding an equilibrium's loss needs a link
  between the ψ_k and φ that the remainder of φ does not carry. T6 claims
  nothing there.

Executable form: `test_t4_t6.py`. For random shared objectives it checks
that equilibria are exactly the coordinate-wise minima and that every
four-cycle sums to zero; it also checks that aligned local objectives keep
the equilibria, and both counterexamples.

## T4 (a bound over a whole run): the plan's conjecture fails; two drafts hold

*Formal setting (as in `t4_check.py`).* Grid G = {0, ..., N−1}^K; box radius
r; B(x) = {y ∈ G : ‖y − x‖_∞ ≤ r}. Objectives φ_0, ..., φ_{T−1} on G, every
point feasible, each with a unique minimiser m_t. The joint step J_t(x) is
the first minimiser of φ_t over B(x) (`joint_choice`). The per-knob step
D_t(x) is the per-knob choice on B(x) anchored at x (`per_knob_choice`).
I_t(x) is the index of φ_t on B(x) anchored at x. Both runs start at x_0:
x^J_{t+1} = J_t(x^J_t) and x^D_{t+1} = D_t(x^D_t). The excess is
E_T = Σ_{t<T} [φ_t(x^D_{t+1}) − φ_t(x^J_{t+1})]. Unless stated otherwise,
I_t means I_t(x^D_t), the index along the per-knob run.

*The plan's assumptions, formalised for a metric d, either ℓ∞ or ℓ1:*
(a) ‖m_{t+1} − m_t‖_∞ ≤ r.
(b) φ_t(x) − φ_t(m_t) ≥ α d(x, m_t) for all x, and |φ_t(x) − φ_t(y)| ≤ β
whenever d(x, y) = 1 (king moves for ℓ∞, unit moves for ℓ1). The checks use
the tightest α > 0 and β over all t, which gives the smallest constant the
conjecture allows.
(c) For every t and every x ∈ G: d(J_t(x), m_t) ≤ d(x, m_t) and
d(D_t(x), m_t) ≤ d(x, m_t).

*Conjecture (plan).* E_T ≤ (1 + β/α) Σ_{t<T} I_t.

*Status: false.* Counterexample family (draft hand computation; computed
by `python t4_check.py drift` and recomputed step by step with
`analyse_box` in `test_t4_t6.py`). Take T ≥ 2, K = 2, r = 1, N = T + 3,
m_t = (T − t, T − t) and x_0 = m_0 + (1, 1). With z = x − m_t, the
objectives are φ_0 = |z₁| + |z₂| + 1.01 |z₁ − z₂| and, for t ≥ 1,
φ_t = |z₁| + |z₂|. Conditions (a), (b) and (c) hold in both metrics,
checked on the whole grid; α = 1, and β = 4.02 (ℓ∞) or 2.01 (ℓ1).

At step 0, either knob alone would climb out of the valley z₁ = z₂, so the
per-knob run stays at x_0 at cost 2, while the joint run lands on m_0;
I_0 = 2.02. From step 1 on, the objective is additive (I_t = 0) and the
minimiser moves one diagonal step per step, away from the per-knob run. That
run also moves one diagonal step per step, so it stays one step behind and
pays 2 at every step. Hence E_T = 2T while Σ I_t = 2.02, and E_T / Σ I_t =
T / 1.01 is unbounded. The conjectured constant (5.02 in ℓ∞, 3.01 in ℓ1)
fails from T = 6 (ℓ∞) and from T = 4 (ℓ1).

Random check (`t4_check.py target`, 100,000 instances satisfying (a)-(c);
`PROPERTY_CHECKS.md`): 46 of the 100,000 accepted instances violate it with the index along the per-knob run (20 in the l_inf metric, 41 in l_1; largest E_T / ((1 + beta/alpha) sum I_t) = 2.67); every violation has a valley or ridge interaction, and most a drifting or adversarial minimiser. Violations need a deviation
followed by a minimiser that moves away, so they are rare in a broad random
mix; rare does not mean true. Smallest counterexample found: from a start within reach, T = 3 on a 4 x 4 grid (l_inf, draw 30401532); from anywhere, T = 4 on a 4 x 4 grid (draw 40452390: E_T = 9 against (1 + beta/alpha) sum I_t = 8).

*Why it fails.* The index sees one decision. Once the runs separate, the
per-knob run is in a different state. (c) forbids a greedy step from
moving away from the minimiser but does not force progress towards it, and
(a) lets the minimiser move as fast as a run can. A lag created once can
then cost something at every later step, with no interaction left for I_t
to show.

*Proposition T4a (two steps), draft.* If ‖x_0 − m_0‖_∞ ≤ r and (a) and (b)
hold, (c) not needed, then E_1 ≤ I_0 and E_2 ≤ (1 + β/α) I_0 + I_1.

*Proof (draft).* Step 0: m_0 ∈ B(x_0) is the unique minimiser, so
x^J_1 = m_0. Theorem 1 at anchor x_0 gives
e_0 = φ_0(x^D_1) − φ_0(m_0) ≤ I_0, and growth gives d(x^D_1, m_0) ≤ e_0/α.
Step 1: x^J_1 = m_0 and ‖m_1 − m_0‖_∞ ≤ r, so x^J_2 = m_1. Let y be m_1
clipped coordinate-wise into B(x^D_1). Then
|y_k − m_{1,k}| = max(0, |x^D_{1,k} − m_{1,k}| − r) ≤ |x^D_{1,k} − m_{0,k}|,
so d(y, m_1) ≤ d(x^D_1, m_0) in either metric. Theorem 1 at anchor x^D_1
and the per-step bound along a monotone lattice path from y to m_1 give
φ_1(x^D_2) ≤ φ_1(y) + I_1 ≤ φ_1(m_1) + β d(y, m_1) + I_1. Hence
e_1 ≤ (β/α) e_0 + I_1, and E_2 = e_0 + e_1 ≤ (1 + β/α) I_0 + I_1. ∎

So 1 + β/α is the right constant for one deviation followed by one step of
catching up. From a start within reach the conjecture cannot fail before
T = 3, and it fails from T = 3 on: one step of lag is paid for, the second
is not. Checked: 60,764 instances with a start within reach, in each metric: 0 violations of T4a.

*Theorem T4' (within reach), draft.* Assume:

- ‖x_0 − m_0‖_∞ ≤ r;
- ‖m_{t+1} − m_t‖_∞ ≤ ρ ≤ r for all t;
- φ_t(x) − φ_t(m_t) ≥ α ‖x − m_t‖_∞ for all t and x, with α > 0;
- (e) I_t(x) ≤ α (r − ρ) for every t and every x with ‖x − m_t‖_∞ ≤ r.

Then for every t: ‖x^D_t − m_t‖_∞ ≤ r, x^J_{t+1} = m_t, and
0 ≤ φ_t(x^D_{t+1}) − φ_t(m_t) ≤ I_t(x^D_t). Hence
0 ≤ E_T ≤ Σ_{t<T} I_t(x^D_t), with constant 1, and the joint run is the
clairvoyant one.

*Proof (draft).* By induction on t. If ‖x^D_t − m_t‖_∞ ≤ r, then
m_t ∈ B(x^D_t) is the minimum of the box. Theorem 1 at anchor x^D_t gives
0 ≤ φ_t(x^D_{t+1}) − φ_t(m_t) ≤ I_t(x^D_t) ≤ α (r − ρ). By growth,
‖x^D_{t+1} − m_t‖_∞ ≤ r − ρ, so ‖x^D_{t+1} − m_{t+1}‖_∞ ≤ r. For the joint
run: if ‖x^J_t − m_t‖_∞ ≤ r, then x^J_{t+1} = m_t, the unique minimiser in
the box, and ‖m_t − m_{t+1}‖_∞ ≤ ρ ≤ r. Both hold at t = 0 by the
assumption on x_0. Summing the per-step bound gives the claim. ∎

Checked: 17,000 instances drawn under (e) and the other assumptions, 14,568 accepted: 0 failures of the invariant, of the per-step bound or of E_T <= sum I_t (13,694 with a non-zero index, 354 with a positive excess).

*What T4' says and does not say.* Its condition is that the interaction is
small compared with growth times tracking slack. Then per-knob control
never loses track of the moving optimum, and its excess over a whole run is
at most the summed index along its own run. When the minimiser moves at
full box speed (ρ = r), (e) forces I = 0 within reach, and the runs coincide.
T4' says nothing when (e) fails; the counterexample shows that then no bound
in terms of Σ I_t holds in general. Whether the planner's decisions satisfy
(e) is open. Its objective is not known to have this form (a unique
minimiser with linear growth on a grid), and (e) has not been measured on it.

Under the plan's rule, T4 does not enter the paper as a theorem. The
conjecture is refuted; T4a and T4' are drafts awaiting a second reader.

References.
D. Monderer and L. S. Shapley, "Potential games", Games and Economic
Behavior 14 (1996) 124–143.
R. Radner, "Team decision problems", Annals of Mathematical Statistics 33
(1962) 857–881.
