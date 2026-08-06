package controllers

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
)

const (
	// ConditionApplied is the Policy status condition the operator maintains.
	ConditionApplied = "Applied"

	// targetMissingRequeue is how long to wait for a Deployment that does
	// not exist yet — the tenant's workload may still be rolling out.
	targetMissingRequeue = 30 * time.Second
)

// PolicyReconciler applies a Policy to the cluster: Deployment replicas,
// per-tenant cache/model-tier ConfigMap. Replica counts are clamped to
// [replicaMin, replicaMax] before anything is written — the guardrail lives
// here so no plan source (human or planner) can bypass it. When several
// Policies name the same target Deployment (pool tenants sharing a data
// plane), the Deployment is scaled to the sum of their clamped
// contributions; Status.AppliedReplicas remains each tenant's own share.
type PolicyReconciler struct {
	client.Client
	// GatewayKnobs, when set, pushes the Policy's cache/tier levers to the
	// live data plane (the AI gateway's admin endpoint). A failed push keeps
	// Applied false: the actuation gate must cover every knob the plan
	// claims to have applied, or a run could score an inert-knob plan as
	// actuated (PREREG_WAVE4_LIVE_PLANE.md §Substrate).
	GatewayKnobs GatewayKnobsPusher
}

func (r *PolicyReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	ctx, span := tracer.Start(ctx, "policy.reconcile")
	defer span.End()
	span.SetAttributes(attribute.String("policy", req.Name))

	var policy pfv1alpha1.Policy
	if err := r.Get(ctx, req.NamespacedName, &policy); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	replicas := clampReplicas(&policy.Spec)
	cacheMB := clampCache(&policy.Spec)
	tier := clampTier(&policy.Spec)
	span.SetAttributes(
		attribute.String("tenant", policy.Spec.TenantRef),
		attribute.Int("replicas", int(replicas)),
		attribute.Int("cache_mb", int(cacheMB)),
		attribute.String("model_tier", string(tier)),
	)

	ns, name, err := splitTarget(policy.Spec.TargetDeployment, policy.Spec.TenantRef)
	if err != nil {
		r.setApplied(&policy, metav1.ConditionFalse, "InvalidTarget", err.Error())
		return ctrl.Result{}, r.Status().Update(ctx, &policy)
	}

	total, err := r.sharedTargetReplicas(ctx, ns, name)
	if err != nil {
		span.SetStatus(codes.Error, err.Error())
		return ctrl.Result{}, err
	}

	if err := r.scaleDeployment(ctx, ns, name, total); err != nil {
		if apierrors.IsNotFound(err) {
			r.setApplied(&policy, metav1.ConditionFalse, "TargetMissing",
				fmt.Sprintf("deployment %s/%s does not exist yet", ns, name))
			if serr := r.Status().Update(ctx, &policy); serr != nil {
				return ctrl.Result{}, serr
			}
			return ctrl.Result{RequeueAfter: targetMissingRequeue}, nil
		}
		span.SetStatus(codes.Error, err.Error())
		return ctrl.Result{}, err
	}

	if err := r.ensureTenantConfig(ctx, ns, &policy, cacheMB, tier); err != nil {
		span.SetStatus(codes.Error, err.Error())
		return ctrl.Result{}, err
	}

	if r.GatewayKnobs != nil {
		if err := r.GatewayKnobs.Push(ctx, policy.Spec.TenantRef,
			string(tier), cacheMB); err != nil {
			r.setApplied(&policy, metav1.ConditionFalse, "GatewayKnobsFailed", err.Error())
			if serr := r.Status().Update(ctx, &policy); serr != nil {
				return ctrl.Result{}, serr
			}
			return ctrl.Result{RequeueAfter: 15 * time.Second}, nil
		}
	}

	policy.Status.AppliedReplicas = replicas
	policy.Status.AppliedCacheSizeMB = cacheMB
	policy.Status.AppliedModelTier = tier
	policy.Status.ObservedGeneration = policy.Generation
	if policy.Status.LastPlanSource == "" {
		policy.Status.LastPlanSource = pfv1alpha1.PlanSourceManual
	}
	r.setApplied(&policy, metav1.ConditionTrue, "Applied",
		fmt.Sprintf("replicas=%d (target total %d) cacheMB=%d tier=%s",
			replicas, total, cacheMB, tier))
	if err := r.Status().Update(ctx, &policy); err != nil {
		return ctrl.Result{}, err
	}
	return ctrl.Result{}, nil
}

// clampReplicas enforces the safety bounds. A max below min is treated as a
// misconfiguration in favor of the floor: availability beats cost.
func clampReplicas(spec *pfv1alpha1.PolicySpec) int32 {
	replicas := spec.Replicas
	if replicas < spec.ReplicaMin {
		replicas = spec.ReplicaMin
	}
	if spec.ReplicaMax >= spec.ReplicaMin && replicas > spec.ReplicaMax {
		replicas = spec.ReplicaMax
	}
	return replicas
}

// clampCache enforces the cache-knob bounds, symmetric with clampReplicas.
// A CacheSizeMBMax of 0 means "no ceiling" (the pre-bounds default), so an
// unset bound never zeroes the cache; a positive max caps it, and min==max
// pins it (the cache-frozen ablation posture). A max below a positive min is
// a misconfiguration resolved in favor of the floor.
func clampCache(spec *pfv1alpha1.PolicySpec) int32 {
	cache := spec.CacheSizeMB
	if cache < spec.CacheSizeMBMin {
		cache = spec.CacheSizeMBMin
	}
	if spec.CacheSizeMBMax > 0 && spec.CacheSizeMBMax >= spec.CacheSizeMBMin && cache > spec.CacheSizeMBMax {
		cache = spec.CacheSizeMBMax
	}
	return cache
}

// clampTier enforces the tier-knob bounds on the ordinal none<small<mid<large.
// An empty ModelTierMin/Max means "no bound" (the pre-bounds default), so an
// unset bound never moves the tier; min==max pins it (the tier-frozen ablation
// posture). A max below min is resolved in favor of the floor, matching the
// replica guardrail's availability-beats-cost convention.
func clampTier(spec *pfv1alpha1.PolicySpec) pfv1alpha1.ModelTier {
	tier := spec.ModelTier
	rank := pfv1alpha1.TierRank(tier)
	if lo := pfv1alpha1.TierRank(spec.ModelTierMin); lo >= 0 && rank < lo {
		rank = lo
	}
	if hi := pfv1alpha1.TierRank(spec.ModelTierMax); hi >= 0 && hi >= pfv1alpha1.TierRank(spec.ModelTierMin) && rank > hi {
		rank = hi
	}
	if rank < 0 {
		return tier // unknown tier: leave as-is, validation rejects it upstream
	}
	return pfv1alpha1.TierByRank(rank)
}

func splitTarget(target, tenantRef string) (ns, name string, err error) {
	if target == "" {
		return SharedTenantNamespace, "pf-gateway-" + tenantRef, nil
	}
	parts := strings.SplitN(target, "/", 2)
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return "", "", fmt.Errorf("targetDeployment %q must be namespace/name", target)
	}
	return parts[0], parts[1], nil
}

// sharedTargetReplicas sums the clamped replica contribution of every
// Policy that resolves to the same target Deployment. A tenant on its own
// gateway keeps today's semantics (a sum of one). Pool tenants sharing a
// data plane each contribute their clamped share, so the shared Deployment
// is sized for all of them — last-writer-wins would let whichever tenant
// planned last shrink capacity below the other tenants' floors.
func (r *PolicyReconciler) sharedTargetReplicas(ctx context.Context, ns, name string) (int32, error) {
	var list pfv1alpha1.PolicyList
	if err := r.List(ctx, &list); err != nil {
		return 0, fmt.Errorf("list policies: %w", err)
	}
	var total int32
	for i := range list.Items {
		p := &list.Items[i]
		if !p.DeletionTimestamp.IsZero() {
			continue
		}
		pns, pname, err := splitTarget(p.Spec.TargetDeployment, p.Spec.TenantRef)
		if err != nil || pns != ns || pname != name {
			continue
		}
		total += clampReplicas(&p.Spec)
	}
	return total, nil
}

// policiesSharingTarget re-enqueues the siblings of a changed or deleted
// Policy so the shared target's sum is recomputed even when this Policy no
// longer reconciles (deletion is the case For() alone cannot cover).
func (r *PolicyReconciler) policiesSharingTarget(ctx context.Context, obj client.Object) []reconcile.Request {
	policy, ok := obj.(*pfv1alpha1.Policy)
	if !ok {
		return nil
	}
	ns, name, err := splitTarget(policy.Spec.TargetDeployment, policy.Spec.TenantRef)
	if err != nil {
		return nil
	}
	var list pfv1alpha1.PolicyList
	if err := r.List(ctx, &list); err != nil {
		return nil
	}
	var reqs []reconcile.Request
	for i := range list.Items {
		other := &list.Items[i]
		if other.Name == policy.Name {
			continue // For() already enqueues the object itself
		}
		ons, oname, err := splitTarget(other.Spec.TargetDeployment, other.Spec.TenantRef)
		if err == nil && ons == ns && oname == name {
			reqs = append(reqs, reconcile.Request{
				NamespacedName: types.NamespacedName{Name: other.Name},
			})
		}
	}
	return reqs
}

func (r *PolicyReconciler) scaleDeployment(ctx context.Context, ns, name string, replicas int32) error {
	var deploy appsv1.Deployment
	if err := r.Get(ctx, types.NamespacedName{Namespace: ns, Name: name}, &deploy); err != nil {
		return err
	}
	if deploy.Spec.Replicas != nil && *deploy.Spec.Replicas == replicas {
		return nil
	}
	patch := client.MergeFrom(deploy.DeepCopy())
	deploy.Spec.Replicas = &replicas
	return r.Patch(ctx, &deploy, patch)
}

// ensureTenantConfig writes the per-tenant knobs the gateway reads at
// runtime: semantic-cache budget (W28 eviction bound) and model tier
// (W19 routing table). The values are the clamped ones — the guardrail is
// enforced at the single actuation point, so a pinned (min==max) knob is
// physically frozen at the data plane regardless of the plan source.
func (r *PolicyReconciler) ensureTenantConfig(ctx context.Context, ns string, policy *pfv1alpha1.Policy, cacheMB int32, tier pfv1alpha1.ModelTier) error {
	name := "pf-tenant-config-" + policy.Spec.TenantRef
	data := map[string]string{
		"cache_size_mb": strconv.Itoa(int(cacheMB)),
		"model_tier":    string(tier),
	}
	var cm corev1.ConfigMap
	err := r.Get(ctx, types.NamespacedName{Namespace: ns, Name: name}, &cm)
	if apierrors.IsNotFound(err) {
		return r.Create(ctx, &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: ns,
				Labels:    map[string]string{managedByLabel: managedByValue, tenantLabel: policy.Spec.TenantRef},
			},
			Data: data,
		})
	}
	if err != nil {
		return err
	}
	if cm.Data["cache_size_mb"] == data["cache_size_mb"] && cm.Data["model_tier"] == data["model_tier"] {
		return nil
	}
	patch := client.MergeFrom(cm.DeepCopy())
	cm.Data = data
	return r.Patch(ctx, &cm, patch)
}

func (r *PolicyReconciler) setApplied(policy *pfv1alpha1.Policy, status metav1.ConditionStatus, reason, message string) {
	meta.SetStatusCondition(&policy.Status.Conditions, metav1.Condition{
		Type:               ConditionApplied,
		Status:             status,
		Reason:             reason,
		Message:            message,
		ObservedGeneration: policy.Generation,
	})
}

func (r *PolicyReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&pfv1alpha1.Policy{}).
		Owns(&corev1.ConfigMap{}).
		Watches(&pfv1alpha1.Policy{}, handler.EnqueueRequestsFromMapFunc(r.policiesSharingTarget)).
		Complete(r)
}
