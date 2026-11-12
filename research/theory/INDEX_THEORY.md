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
