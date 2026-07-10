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
// here so no plan source (human or planner) can bypass it.
type PolicyReconciler struct {
	client.Client
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
	span.SetAttributes(
		attribute.String("tenant", policy.Spec.TenantRef),
		attribute.Int("replicas", int(replicas)),
		attribute.Int("cache_mb", int(policy.Spec.CacheSizeMB)),
		attribute.String("model_tier", string(policy.Spec.ModelTier)),
	)

	ns, name, err := splitTarget(policy.Spec.TargetDeployment, policy.Spec.TenantRef)
	if err != nil {
		r.setApplied(&policy, metav1.ConditionFalse, "InvalidTarget", err.Error())
		return ctrl.Result{}, r.Status().Update(ctx, &policy)
	}

	if err := r.scaleDeployment(ctx, ns, name, replicas); err != nil {
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

	if err := r.ensureTenantConfig(ctx, ns, &policy); err != nil {
		span.SetStatus(codes.Error, err.Error())
		return ctrl.Result{}, err
	}

	policy.Status.AppliedReplicas = replicas
	policy.Status.AppliedCacheSizeMB = policy.Spec.CacheSizeMB
	policy.Status.AppliedModelTier = policy.Spec.ModelTier
	policy.Status.ObservedGeneration = policy.Generation
	if policy.Status.LastPlanSource == "" {
		policy.Status.LastPlanSource = pfv1alpha1.PlanSourceManual
	}
	r.setApplied(&policy, metav1.ConditionTrue, "Applied",
		fmt.Sprintf("replicas=%d cacheMB=%d tier=%s", replicas, policy.Spec.CacheSizeMB, policy.Spec.ModelTier))
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
// (W19 routing table).
func (r *PolicyReconciler) ensureTenantConfig(ctx context.Context, ns string, policy *pfv1alpha1.Policy) error {
	name := "pf-tenant-config-" + policy.Spec.TenantRef
	data := map[string]string{
		"cache_size_mb": strconv.Itoa(int(policy.Spec.CacheSizeMB)),
		"model_tier":    string(policy.Spec.ModelTier),
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
		Complete(r)
}
