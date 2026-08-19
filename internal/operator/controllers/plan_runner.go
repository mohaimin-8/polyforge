package controllers

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/util/retry"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/metrics"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
	"polyforge/internal/operator/fairness"
	"polyforge/internal/operator/planner"
)

// jainGauge reports cluster-wide fairness over per-tenant SLO satisfaction
// after every plan cycle (W32 verification: Jain's index visible over
// time, dropping when a tenant goes noisy and recovering post-mitigation).
var jainGauge = prometheus.NewGauge(prometheus.GaugeOpts{
	Name: "polyforge_operator_fairness_jain",
	Help: "Jain's fairness index over per-tenant projected SLO satisfaction, per plan cycle.",
})

func init() {
	metrics.Registry.MustRegister(jainGauge)
}

const (
	// DefaultPlanInterval is JCAC's control period: plan, apply the first
	// action, re-plan. Matches HORIZON re-planning in the W30 design.
	DefaultPlanInterval = 10 * time.Second

	planCallTimeout = 3 * time.Second

	// AuditSubjectPrefix puts operator actions on the existing audit
	// stream (internal/events, SubjectRoot polyforge.audit).
	AuditSubjectPrefix = "polyforge.audit.operator.plan."
)

// AuditPublisher is satisfied by *events.Backbone (NATS JetStream); tests
// use an in-memory recorder. Every applied or fallback plan is published.
type AuditPublisher interface {
	Publish(ctx context.Context, subject, msgID string, data []byte) error
}

// InterferenceSource supplies the W32 noisy-neighbor score per tenant
// (0 = clean). Optional; nil means no interference term in the plan.
type InterferenceSource interface {
	TenantInterference(ctx context.Context, tenantID string) float64
}

// AuditEntry is the JSON payload logged for every planner action, carrying
// input state, output action, and the planner's own $-impact estimate.
type AuditEntry struct {
	Time               time.Time     `json:"time"`
	Tenant             string        `json:"tenant"`
	Source             string        `json:"source"` // planner | fallback
	Solver             string        `json:"solver,omitempty"`
	From               planner.State `json:"from"`
	To                 planner.State `json:"to"`
	ProjectedCostUSD   float64       `json:"projected_cost_usd"`
	ProjectedViolation float64       `json:"projected_violation"`
	Interference       float64       `json:"interference,omitempty"`
	Error              string        `json:"error,omitempty"`
}

// PlanRunner drives the JCAC loop: every interval it gathers the state of
// all managed tenants, asks the planner for one *joint* plan, and writes
// the chosen actions into Policy specs (the PolicyReconciler applies them
// to the cluster). Planner failure of any kind leaves specs untouched —
// the last good plan simply keeps running — and flips status to
// "fallback" so the degradation is visible in kubectl.
type PlanRunner struct {
	Client       client.Client
	Planner      planner.Client
	Demands      planner.DemandSource
	Interference InterferenceSource // optional
	Audit        AuditPublisher     // optional
	Log          *slog.Logger
	Interval     time.Duration
	Weights      planner.Weights
	Limits       planner.Limits
	// CellSize caps how many tenants go into one planner call. 0 (the
	// default) plans the whole portfolio in a single call, exactly as before
	// cells existed. Set it — CellSize is the measured value — when the
	// portfolio is large enough that the joint solve approaches the
	// operator's timeout; see planning_cells.go for what it costs.
	CellSize int
}

// Start satisfies manager.Runnable; the manager cancels ctx on shutdown.
func (p *PlanRunner) Start(ctx context.Context) error {
	interval := p.Interval
	if interval <= 0 {
		interval = DefaultPlanInterval
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if err := p.RunOnce(ctx); err != nil && p.Log != nil {
				p.Log.Error("plan cycle failed", "error", err)
			}
		}
	}
}

// RunOnce executes a single control cycle. Exported for tests and for a
// future CLI `operator plan --once`.
func (p *PlanRunner) RunOnce(ctx context.Context) error {
	ctx, span := tracer.Start(ctx, "planner.cycle")
	defer span.End()

	inputs, policies, err := p.gather(ctx)
	if err != nil {
		span.SetStatus(codes.Error, err.Error())
		return err
	}
	if len(inputs) == 0 {
		return nil // nothing has demand yet
	}
	span.SetAttributes(attribute.Int("tenants", len(inputs)))

	// One planner call per planning cell. With CellSize unset this is a
	// single cell holding every tenant — the pre-cells behaviour, unchanged.
	cells := planningCells(inputs, p.CellSize)
	limits := cellLimits(p.Limits, cells)
	span.SetAttributes(attribute.Int("cells", len(cells)))

	resp := planner.Response{Plans: make(map[string]planner.Plan, len(inputs))}
	for i, cell := range cells {
		planCtx, cancel := context.WithTimeout(ctx, planCallTimeout)
		cellResp, err := p.Planner.Plan(planCtx, planner.Request{
			Weights: p.Weights,
			Limits:  limits[i],
			Tenants: cell,
		})
		cancel()
		if err != nil {
			// A cell failing is a cycle failing: applying the cells that did
			// succeed would actuate a partial plan whose fairness term was
			// solved over a portfolio the cluster is not actually in. The
			// designed fallback — hold the last good plan — is the honest
			// response for every tenant, not just the unplanned ones.
			span.SetStatus(codes.Error, err.Error())
			p.fallback(ctx, inputs, policies, err)
			return nil // fallback is the designed behavior, not a cycle error
		}
		resp.Solver = cellResp.Solver
		for id, plan := range cellResp.Plans {
			resp.Plans[id] = plan
		}
	}

	satisfactions := make([]float64, 0, len(inputs))
	for _, input := range inputs {
		plan := resp.Plans[input.TenantID]
		satisfactions = append(satisfactions, 1.0-plan.ProjectedViolation)
		if err := p.apply(ctx, policies[input.TenantID], input, plan, resp.Solver); err != nil && p.Log != nil {
			p.Log.Error("apply plan", "tenant", input.TenantID, "error", err)
		}
	}
	jainGauge.Set(fairness.JainIndex(satisfactions))
	return nil
}

// gather assembles planner inputs from the Tenant/Policy/Budget objects
// plus live demand. Tenants without telemetry are skipped, not zeroed.
func (p *PlanRunner) gather(ctx context.Context) (inputs []planner.TenantInput, policies map[string]*pfv1alpha1.Policy, err error) {
	var tenants pfv1alpha1.TenantList
	if err := p.Client.List(ctx, &tenants); err != nil {
		return nil, nil, fmt.Errorf("list tenants: %w", err)
	}
	policies = make(map[string]*pfv1alpha1.Policy)
	for i := range tenants.Items {
		tenant := &tenants.Items[i]
		if !tenant.DeletionTimestamp.IsZero() {
			continue
		}
		var policy pfv1alpha1.Policy
		if err := p.Client.Get(ctx, types.NamespacedName{Name: tenant.Name}, &policy); err != nil {
			if apierrors.IsNotFound(err) {
				continue // tenant reconciler has not created defaults yet
			}
			return nil, nil, err
		}
		demand, ok, err := p.Demands.TenantDemand(ctx, tenant.Name)
		if err != nil || !ok {
			continue
		}

		input := planner.TenantInput{
			TenantID:        tenant.Name,
			SLOClass:        string(tenant.Spec.SLOClass),
			HourlyBudgetUSD: 5.0,
			ReplicaMin:      policy.Spec.ReplicaMin,
			ReplicaMax:      policy.Spec.ReplicaMax,
			FairnessWeight:  0.5,
			// Forward the cache/tier knob bounds so the planner optimizes over
			// the same envelope the operator enforces — a pinned (min==max)
			// knob makes the planner re-optimize the free knob alone (the
			// cache-only / tier-only ablation). A zero cache ceiling / empty
			// tier bound is omitempty'd away, so an unbounded Policy sends the
			// pre-bounds request unchanged.
			CacheMin: policy.Spec.CacheSizeMBMin,
			CacheMax: nonZeroInt32Ptr(policy.Spec.CacheSizeMBMax),
			TierMin:  string(policy.Spec.ModelTierMin),
			TierMax:  string(policy.Spec.ModelTierMax),
			State: planner.State{
				Replicas: policy.Spec.Replicas,
				CacheMB:  policy.Spec.CacheSizeMB,
				Tier:     string(policy.Spec.ModelTier),
			},
			Demand: demand,
		}
		// A missing Budget legitimately means "use the default". Any OTHER
		// error — apiserver timeout, RBAC denial, APF throttle (which the
		// chaos campaign deliberately induces) — must not silently replace
		// the tenant's real cap with the $5/hr default: the budget is the
		// constraint the planner optimizes under and, in the cost arm, the
		// experiment's independent variable. Skipping the tenant holds its
		// last good plan, which is the honest response to not knowing.
		var budget pfv1alpha1.Budget
		switch err := p.Client.Get(ctx, types.NamespacedName{Name: tenant.Name}, &budget); {
		case err == nil:
			input.HourlyBudgetUSD = float64(budget.Spec.HourlyCapMilliUSD) / 1000.0
			input.FairnessWeight = float64(budget.Spec.FairnessWeightPermille) / 1000.0
		case apierrors.IsNotFound(err):
			// Defaults already set above; no Budget CR for this tenant.
		default:
			if p.Log != nil {
				p.Log.Error("read budget; skipping tenant this cycle rather "+
					"than planning against the default cap",
					"tenant", tenant.Name, "error", err)
			}
			continue
		}
		if p.Interference != nil {
			input.Interference = p.Interference.TenantInterference(ctx, tenant.Name)
		}
		inputs = append(inputs, input)
		policies[tenant.Name] = &policy
	}
	return inputs, policies, nil
}

// apply writes the planned action into the Policy spec. The clamp is a
// second line of defense: the planner already respects bounds, but no
// plan source is trusted to (safety guardrail, same rule as the
// PolicyReconciler).
func (p *PlanRunner) apply(ctx context.Context, policy *pfv1alpha1.Policy, input planner.TenantInput, plan planner.Plan, solver string) error {
	// Reuse the shared clamp rather than open-coding it. The inline copy
	// omitted clampReplicas's `ReplicaMax >= ReplicaMin` guard, so on an
	// inverted band (now rejected by CEL, but older objects persist) this
	// path wrote a different value than PolicyReconciler actuated — and the
	// next cycle read the wrong one back as the planner's "from" state, so
	// the recorded trajectory and the actuated one diverged permanently.
	replicaSpec := policy.Spec
	replicaSpec.Replicas = plan.Replicas
	replicas := clampReplicas(&replicaSpec)
	// Clamp cache/tier to the Policy bounds too, reusing the shared clamp
	// helpers on a copy carrying the planned value — the single source of the
	// bound logic. The planner already plans within them, so with a bound-free
	// Policy this is the identity; with a pin it is the belt to the planner's
	// braces, matching the replica guardrail above.
	cacheSpec := policy.Spec
	cacheSpec.CacheSizeMB = plan.CacheMB
	cache := clampCache(&cacheSpec)
	tierSpec := policy.Spec
	tierSpec.ModelTier = pfv1alpha1.ModelTier(plan.Tier)
	tier := clampTier(&tierSpec)

	changed := policy.Spec.Replicas != replicas ||
		policy.Spec.CacheSizeMB != cache ||
		policy.Spec.ModelTier != tier
	if changed {
		policy.Spec.Replicas = replicas
		policy.Spec.CacheSizeMB = cache
		policy.Spec.ModelTier = tier
		// Retry on conflict: PolicyReconciler writes this object's status on
		// every reconcile, so the resourceVersion we planned against is
		// routinely stale by the time we write. Without this the plan was
		// simply dropped for the cycle and the tenant silently kept the
		// previous configuration — invisible against a fake client, which
		// does not enforce optimistic concurrency.
		if err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
			if err := p.Client.Update(ctx, policy); err != nil {
				if !apierrors.IsConflict(err) {
					return err
				}
				var fresh pfv1alpha1.Policy
				key := types.NamespacedName{Name: policy.Name, Namespace: policy.Namespace}
				if getErr := p.Client.Get(ctx, key, &fresh); getErr != nil {
					return getErr
				}
				fresh.Spec.Replicas = replicas
				fresh.Spec.CacheSizeMB = cache
				fresh.Spec.ModelTier = tier
				*policy = fresh
				return p.Client.Update(ctx, policy)
			}
			return nil
		}); err != nil {
			return fmt.Errorf("update policy spec: %w", err)
		}
	}

	// The audit entry is emitted before the status write, and its error is
	// not allowed to suppress it. The status subresource is contended: the
	// spec Update above bumps resourceVersion and wakes PolicyReconciler,
	// which writes status too, so a 409 here is routine. Emitting afterwards
	// meant a conflict silently dropped the record — and only on the
	// `changed` branch, i.e. exactly on the cycles where a knob actually
	// moved. The audit stream is the evidence that the controller acted, so
	// that loss was both silent and biased toward under-reporting actuation.
	now := metav1.Now()
	p.audit(ctx, AuditEntry{
		Time:   now.Time,
		Tenant: input.TenantID,
		Source: string(pfv1alpha1.PlanSourcePlanner),
		Solver: solver,
		From:   input.State,
		To: planner.State{
			Replicas: replicas,
			CacheMB:  cache,
			Tier:     string(tier),
		},
		ProjectedCostUSD:   plan.ProjectedCostUSD,
		ProjectedViolation: plan.ProjectedViolation,
		Interference:       input.Interference,
	})

	// Re-read on conflict rather than clobbering: another writer's status
	// update must not be lost, and ours must not be dropped.
	name := types.NamespacedName{Name: policy.Name, Namespace: policy.Namespace}
	if err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		var fresh pfv1alpha1.Policy
		if err := p.Client.Get(ctx, name, &fresh); err != nil {
			return err
		}
		fresh.Status.LastPlanSource = pfv1alpha1.PlanSourcePlanner
		fresh.Status.LastPlanTime = &now
		return p.Client.Status().Update(ctx, &fresh)
	}); err != nil {
		return fmt.Errorf("update policy status: %w", err)
	}
	policy.Status.LastPlanSource = pfv1alpha1.PlanSourcePlanner
	policy.Status.LastPlanTime = &now
	return nil
}

// nonZeroInt32Ptr returns a pointer to v, or nil when v is 0 — the encoding
// the planner request uses to mean "no cache ceiling" (omitempty drops nil),
// so an unbounded Policy sends no cache_max and the planner applies its
// full-envelope default.
func nonZeroInt32Ptr(v int32) *int32 {
	if v == 0 {
		return nil
	}
	return &v
}

// fallback marks every planned tenant as running on its last good plan.
// Specs are deliberately untouched: the previous plan stays applied.
func (p *PlanRunner) fallback(ctx context.Context, inputs []planner.TenantInput, policies map[string]*pfv1alpha1.Policy, cause error) {
	if p.Log != nil {
		p.Log.Warn("planner unavailable, holding last good plan", "error", cause)
	}
	for _, input := range inputs {
		policy := policies[input.TenantID]
		// Every degraded cycle is audited, including consecutive ones. The
		// status write is still skipped when the mark is already set — that
		// is what the "avoid a status write per cycle" optimisation was for —
		// but skipping the audit with it made a one-hour planner outage
		// indistinguishable from a single blip: both left exactly one entry
		// in the stream that any availability claim is measured from.
		if policy.Status.LastPlanSource != pfv1alpha1.PlanSourceFallback {
			policy.Status.LastPlanSource = pfv1alpha1.PlanSourceFallback
			// The one status write in this file that did not retry. A
			// conflict here loses the fallback mark entirely -- the policy
			// controller reconciles constantly, so a collision is likely
			// exactly when the planner is down and this matters most.
			name := types.NamespacedName{Namespace: policy.Namespace, Name: policy.Name}
			if err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
				var fresh pfv1alpha1.Policy
				if err := p.Client.Get(ctx, name, &fresh); err != nil {
					return err
				}
				fresh.Status.LastPlanSource = pfv1alpha1.PlanSourceFallback
				return p.Client.Status().Update(ctx, &fresh)
			}); err != nil && p.Log != nil {
				p.Log.Error("mark fallback", "tenant", input.TenantID, "error", err)
			}
		}
		p.audit(ctx, AuditEntry{
			Time:   time.Now(),
			Tenant: input.TenantID,
			Source: string(pfv1alpha1.PlanSourceFallback),
			From:   input.State,
			To:     input.State,
			Error:  cause.Error(),
		})
	}
}

func (p *PlanRunner) audit(ctx context.Context, entry AuditEntry) {
	if p.Audit == nil {
		return
	}
	data, err := json.Marshal(entry)
	if err != nil {
		return
	}
	msgID := fmt.Sprintf("%s-%d", entry.Tenant, entry.Time.UnixNano())
	if err := p.Audit.Publish(ctx, AuditSubjectPrefix+entry.Tenant, msgID, data); err != nil && p.Log != nil {
		p.Log.Error("audit publish", "tenant", entry.Tenant, "error", err)
	}
}
