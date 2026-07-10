// Package controllers holds the PolyForge operator reconcile loops.
package controllers

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
)

const (
	// TenantFinalizer guards ordered teardown: dependent CRs first, then
	// the tenant namespace, then the Tenant object itself.
	TenantFinalizer = "polyforge.io/tenant-cleanup"

	// SharedTenantNamespace hosts pool and bridge tenants; only silo
	// tenants get a namespace of their own (ADR 0008 isolation ladder).
	SharedTenantNamespace = "polyforge-tenants"

	// ConditionReady is the Tenant status condition the operator maintains.
	ConditionReady = "Ready"

	managedByLabel = "app.kubernetes.io/managed-by"
	managedByValue = "polyforge-operator"
	tenantLabel    = "polyforge.io/tenant"
)

var tracer = otel.Tracer("polyforge/operator")

// TenantReconciler ensures each Tenant has its namespace, RBAC, and default
// Policy/Budget/WorkloadProfile, and tears them down in order on delete.
type TenantReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// TenantNamespace returns the namespace a tenant's workloads run in.
func TenantNamespace(t *pfv1alpha1.Tenant) string {
	if t.Spec.IsolationMode == pfv1alpha1.IsolationSilo {
		return "pf-tenant-" + t.Name
	}
	return SharedTenantNamespace
}

func (r *TenantReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	ctx, span := tracer.Start(ctx, "tenant.reconcile")
	defer span.End()
	span.SetAttributes(attribute.String("tenant", req.Name))

	var tenant pfv1alpha1.Tenant
	if err := r.Get(ctx, req.NamespacedName, &tenant); err != nil {
		// Deleted after the finalizer ran; nothing left to do.
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	span.SetAttributes(attribute.String("isolation", string(tenant.Spec.IsolationMode)))

	if !tenant.DeletionTimestamp.IsZero() {
		if err := r.finalize(ctx, &tenant); err != nil {
			span.SetStatus(codes.Error, err.Error())
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	if controllerutil.AddFinalizer(&tenant, TenantFinalizer) {
		if err := r.Update(ctx, &tenant); err != nil {
			return ctrl.Result{}, err
		}
	}

	ns := TenantNamespace(&tenant)
	for _, ensure := range []func(context.Context, *pfv1alpha1.Tenant, string) error{
		r.ensureNamespace,
		r.ensureRBAC,
		r.ensureDefaults,
	} {
		if err := ensure(ctx, &tenant, ns); err != nil {
			span.SetStatus(codes.Error, err.Error())
			r.setReady(&tenant, metav1.ConditionFalse, "ReconcileError", err.Error())
			_ = r.Status().Update(ctx, &tenant)
			return ctrl.Result{}, err
		}
	}

	tenant.Status.Namespace = ns
	tenant.Status.ObservedGeneration = tenant.Generation
	now := metav1.Now()
	tenant.Status.LastReconcileTime = &now
	r.setReady(&tenant, metav1.ConditionTrue, "Reconciled", "namespace, RBAC, and default policy objects exist")
	if err := r.Status().Update(ctx, &tenant); err != nil {
		return ctrl.Result{}, err
	}
	return ctrl.Result{}, nil
}

// finalize deletes dependents in an order that never strands a child:
// cluster-scoped CRs referencing the tenant first, then the tenant's own
// namespace (shared namespace is never deleted), then the finalizer.
func (r *TenantReconciler) finalize(ctx context.Context, tenant *pfv1alpha1.Tenant) error {
	if !controllerutil.ContainsFinalizer(tenant, TenantFinalizer) {
		return nil
	}
	for _, obj := range []client.Object{
		&pfv1alpha1.Policy{ObjectMeta: metav1.ObjectMeta{Name: tenant.Name}},
		&pfv1alpha1.Budget{ObjectMeta: metav1.ObjectMeta{Name: tenant.Name}},
		&pfv1alpha1.WorkloadProfile{ObjectMeta: metav1.ObjectMeta{Name: tenant.Name}},
	} {
		if err := r.Delete(ctx, obj); err != nil && !apierrors.IsNotFound(err) {
			return fmt.Errorf("deleting dependent %T: %w", obj, err)
		}
	}
	if tenant.Spec.IsolationMode == pfv1alpha1.IsolationSilo {
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: TenantNamespace(tenant)}}
		if err := r.Delete(ctx, ns); err != nil && !apierrors.IsNotFound(err) {
			return fmt.Errorf("deleting tenant namespace: %w", err)
		}
	}
	controllerutil.RemoveFinalizer(tenant, TenantFinalizer)
	return r.Update(ctx, tenant)
}

func (r *TenantReconciler) ensureNamespace(ctx context.Context, tenant *pfv1alpha1.Tenant, ns string) error {
	var existing corev1.Namespace
	err := r.Get(ctx, types.NamespacedName{Name: ns}, &existing)
	if err == nil {
		return nil
	}
	if !apierrors.IsNotFound(err) {
		return err
	}
	labels := map[string]string{managedByLabel: managedByValue}
	if tenant.Spec.IsolationMode == pfv1alpha1.IsolationSilo {
		labels[tenantLabel] = tenant.Name
	}
	return r.Create(ctx, &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{Name: ns, Labels: labels},
	})
}

// ensureRBAC grants the tenant's service account read-only access to its own
// pods and configmaps — enough for self-service debugging, nothing that
// crosses tenants. Default-deny networking (W24) covers the data plane.
func (r *TenantReconciler) ensureRBAC(ctx context.Context, tenant *pfv1alpha1.Tenant, ns string) error {
	roleName := "pf-tenant-" + tenant.Name
	role := &rbacv1.Role{
		ObjectMeta: metav1.ObjectMeta{
			Name:      roleName,
			Namespace: ns,
			Labels:    map[string]string{managedByLabel: managedByValue, tenantLabel: tenant.Name},
		},
		Rules: []rbacv1.PolicyRule{{
			APIGroups: []string{""},
			Resources: []string{"pods", "configmaps"},
			Verbs:     []string{"get", "list", "watch"},
		}},
	}
	if err := r.Create(ctx, role); err != nil && !apierrors.IsAlreadyExists(err) {
		return err
	}
	binding := &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name:      roleName,
			Namespace: ns,
			Labels:    map[string]string{managedByLabel: managedByValue, tenantLabel: tenant.Name},
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: rbacv1.GroupName,
			Kind:     "Role",
			Name:     roleName,
		},
		Subjects: []rbacv1.Subject{{
			Kind:      rbacv1.ServiceAccountKind,
			Name:      "pf-tenant-" + tenant.Name,
			Namespace: ns,
		}},
	}
	if err := r.Create(ctx, binding); err != nil && !apierrors.IsAlreadyExists(err) {
		return err
	}
	return nil
}

// ensureDefaults creates the Policy, Budget, and WorkloadProfile a tenant
// needs before the planner can manage it. Existing objects are left alone:
// the planner owns their spec after creation.
func (r *TenantReconciler) ensureDefaults(ctx context.Context, tenant *pfv1alpha1.Tenant, ns string) error {
	ownerRef := metav1.OwnerReference{
		APIVersion: pfv1alpha1.GroupVersion.String(),
		Kind:       "Tenant",
		Name:       tenant.Name,
		UID:        tenant.UID,
	}
	meta := metav1.ObjectMeta{
		Name:            tenant.Name,
		Labels:          map[string]string{managedByLabel: managedByValue, tenantLabel: tenant.Name},
		OwnerReferences: []metav1.OwnerReference{ownerRef},
	}
	defaults := []client.Object{
		&pfv1alpha1.Policy{
			ObjectMeta: meta,
			Spec: pfv1alpha1.PolicySpec{
				TenantRef:        tenant.Name,
				TargetDeployment: ns + "/pf-gateway-" + tenant.Name,
				Replicas:         2,
				ReplicaMin:       1,
				ReplicaMax:       10,
				CacheSizeMB:      128,
				ModelTier:        pfv1alpha1.ModelTierSmall,
			},
		},
		&pfv1alpha1.Budget{
			ObjectMeta: meta,
			Spec: pfv1alpha1.BudgetSpec{
				TenantRef:              tenant.Name,
				HourlyCapMilliUSD:      1000,
				FairnessWeightPermille: 500,
			},
		},
		&pfv1alpha1.WorkloadProfile{
			ObjectMeta: meta,
			Spec: pfv1alpha1.WorkloadProfileSpec{
				TenantRef:    tenant.Name,
				HistoryLimit: 12,
			},
		},
	}
	for _, obj := range defaults {
		if err := r.Create(ctx, obj); err != nil && !apierrors.IsAlreadyExists(err) {
			return err
		}
	}
	return nil
}

func (r *TenantReconciler) setReady(tenant *pfv1alpha1.Tenant, status metav1.ConditionStatus, reason, message string) {
	meta.SetStatusCondition(&tenant.Status.Conditions, metav1.Condition{
		Type:               ConditionReady,
		Status:             status,
		Reason:             reason,
		Message:            message,
		ObservedGeneration: tenant.Generation,
	})
}

func (r *TenantReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&pfv1alpha1.Tenant{}).
		Owns(&pfv1alpha1.Policy{}).
		Owns(&pfv1alpha1.Budget{}).
		Owns(&pfv1alpha1.WorkloadProfile{}).
		Complete(r)
}
