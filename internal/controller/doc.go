// Package controller is the control plane's static per-class policy
// heuristic: one fixed replicas/cache/tier recommendation per workload class,
// served by GET /v1/tenants/{id}/policy-recommendation and used to seed a new
// tenant's Policy during onboarding (internal/saga).
//
// It is NOT the joint controller the research evaluates. JCAC -- the MPC over
// replicas, semantic cache and model tier -- lives in research/jcac_sim and
// runs live as the planner service (services/planner), which the operator
// calls on every reconcile (internal/operator/planner). Nothing measured in
// the paper's campaigns comes from this package (audit 2026-09-26).
package controller
