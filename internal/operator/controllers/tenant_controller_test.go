package controllers

import (
	"context"
	"testing"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
)

func newScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	s := runtime.NewScheme()
	if err := clientgoscheme.AddToScheme(s); err != nil {
		t.Fatal(err)
	}
	if err := pfv1alpha1.AddToScheme(s); err != nil {
		t.Fatal(err)
	}
	return s
}

func newTenantClient(t *testing.T, objs ...client.Object) client.WithWatch {
	t.Helper()
	return fake.NewClientBuilder().
		WithScheme(newScheme(t)).
		WithObjects(objs...).
		WithStatusSubresource(&pfv1alpha1.Tenant{}, &pfv1alpha1.Policy{}, &pfv1alpha1.Budget{}, &pfv1alpha1.WorkloadProfile{}).
		Build()
}

func reconcileTenant(t *testing.T, c client.Client, name string) {
	t.Helper()
	r := &TenantReconciler{Client: c}
	// Two passes: the first may stop after adding the finalizer.
	for range 2 {
		if _, err := r.Reconcile(context.Background(), ctrl.Request{
			NamespacedName: types.NamespacedName{Name: name},
		}); err != nil {
			t.Fatalf("reconcile: %v", err)
		}
	}
}

func TestTenantReconcileCreatesNamespaceRBACAndDefaults(t *testing.T) {
	tenant := &pfv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{Name: "acme", UID: "uid-acme"},
		Spec: pfv1alpha1.TenantSpec{
			IsolationMode: pfv1alpha1.IsolationSilo,
			SLOClass:      pfv1alpha1.SLOPremium,
		},
	}
	c := newTenantClient(t, tenant)
	reconcileTenant(t, c, "acme")

	ctx := context.Background()

	var ns corev1.Namespace
	if err := c.Get(ctx, types.NamespacedName{Name: "pf-tenant-acme"}, &ns); err != nil {
		t.Fatalf("silo tenant namespace not created: %v", err)
	}
	if ns.Labels[tenantLabel] != "acme" {
		t.Errorf("namespace missing tenant label, got %v", ns.Labels)
	}

	var role rbacv1.Role
	if err := c.Get(ctx, types.NamespacedName{Namespace: "pf-tenant-acme", Name: "pf-tenant-acme"}, &role); err != nil {
		t.Fatalf("tenant role not created: %v", err)
	}
	var binding rbacv1.RoleBinding
	if err := c.Get(ctx, types.NamespacedName{Namespace: "pf-tenant-acme", Name: "pf-tenant-acme"}, &binding); err != nil {
		t.Fatalf("tenant role binding not created: %v", err)
	}

	var policy pfv1alpha1.Policy
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &policy); err != nil {
		t.Fatalf("default policy not created: %v", err)
	}
	if policy.Spec.TargetDeployment != "pf-tenant-acme/pf-gateway-acme" {
		t.Errorf("default policy target = %q", policy.Spec.TargetDeployment)
	}
	var budget pfv1alpha1.Budget
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &budget); err != nil {
		t.Fatalf("default budget not created: %v", err)
	}
	var profile pfv1alpha1.WorkloadProfile
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &profile); err != nil {
		t.Fatalf("default workload profile not created: %v", err)
	}

	var got pfv1alpha1.Tenant
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Status.Namespace != "pf-tenant-acme" {
		t.Errorf("status.namespace = %q", got.Status.Namespace)
	}
	ready := meta.FindStatusCondition(got.Status.Conditions, ConditionReady)
	if ready == nil || ready.Status != metav1.ConditionTrue {
		t.Errorf("Ready condition = %+v, want True", ready)
	}
	if got.Status.LastReconcileTime == nil {
		t.Error("status.lastReconcileTime not set")
	}
}

func TestPoolTenantSharesNamespace(t *testing.T) {
	tenant := &pfv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{Name: "smallco", UID: "uid-smallco"},
		Spec:       pfv1alpha1.TenantSpec{IsolationMode: pfv1alpha1.IsolationPool},
	}
	c := newTenantClient(t, tenant)
	reconcileTenant(t, c, "smallco")

	var ns corev1.Namespace
	if err := c.Get(context.Background(), types.NamespacedName{Name: SharedTenantNamespace}, &ns); err != nil {
		t.Fatalf("shared namespace not created: %v", err)
	}
	var got pfv1alpha1.Tenant
	if err := c.Get(context.Background(), types.NamespacedName{Name: "smallco"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Status.Namespace != SharedTenantNamespace {
		t.Errorf("status.namespace = %q, want %q", got.Status.Namespace, SharedTenantNamespace)
	}
}

func TestTenantReconcileIsIdempotent(t *testing.T) {
	tenant := &pfv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{Name: "acme", UID: "uid-acme"},
		Spec:       pfv1alpha1.TenantSpec{IsolationMode: pfv1alpha1.IsolationSilo},
	}
	c := newTenantClient(t, tenant)
	reconcileTenant(t, c, "acme")

	// A human retunes the planner-owned policy; re-reconciling the tenant
	// must not stomp it back to defaults.
	var policy pfv1alpha1.Policy
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &policy); err != nil {
		t.Fatal(err)
	}
	policy.Spec.Replicas = 7
	if err := c.Update(context.Background(), &policy); err != nil {
		t.Fatal(err)
	}

	reconcileTenant(t, c, "acme")

	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &policy); err != nil {
		t.Fatal(err)
	}
	if policy.Spec.Replicas != 7 {
		t.Errorf("re-reconcile reset policy replicas to %d, want 7 preserved", policy.Spec.Replicas)
	}
}

func TestTenantFinalizerDeletesDependentsAndNamespace(t *testing.T) {
	tenant := &pfv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{Name: "acme", UID: "uid-acme"},
		Spec:       pfv1alpha1.TenantSpec{IsolationMode: pfv1alpha1.IsolationSilo},
	}
	c := newTenantClient(t, tenant)
	reconcileTenant(t, c, "acme")

	ctx := context.Background()
	var created pfv1alpha1.Tenant
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &created); err != nil {
		t.Fatal(err)
	}
	if err := c.Delete(ctx, &created); err != nil {
		t.Fatal(err)
	}
	// The finalizer blocks actual deletion until the reconciler runs.
	reconcileTenant(t, c, "acme")

	for name, obj := range map[string]client.Object{
		"tenant":          &pfv1alpha1.Tenant{},
		"policy":          &pfv1alpha1.Policy{},
		"budget":          &pfv1alpha1.Budget{},
		"workloadprofile": &pfv1alpha1.WorkloadProfile{},
	} {
		err := c.Get(ctx, types.NamespacedName{Name: "acme"}, obj)
		if !apierrors.IsNotFound(err) {
			t.Errorf("%s still exists after finalize (err=%v)", name, err)
		}
	}
	var ns corev1.Namespace
	err := c.Get(ctx, types.NamespacedName{Name: "pf-tenant-acme"}, &ns)
	// The fake client leaves namespaces in Terminating rather than removing
	// them; accept either as proof the delete was issued.
	if err == nil && ns.DeletionTimestamp.IsZero() {
		t.Error("tenant namespace was not deleted")
	}
}

func TestSharedNamespaceSurvivesTenantDeletion(t *testing.T) {
	tenant := &pfv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{Name: "smallco", UID: "uid-smallco"},
		Spec:       pfv1alpha1.TenantSpec{IsolationMode: pfv1alpha1.IsolationPool},
	}
	c := newTenantClient(t, tenant)
	reconcileTenant(t, c, "smallco")

	ctx := context.Background()
	var created pfv1alpha1.Tenant
	if err := c.Get(ctx, types.NamespacedName{Name: "smallco"}, &created); err != nil {
		t.Fatal(err)
	}
	if err := c.Delete(ctx, &created); err != nil {
		t.Fatal(err)
	}
	reconcileTenant(t, c, "smallco")

	var ns corev1.Namespace
	if err := c.Get(ctx, types.NamespacedName{Name: SharedTenantNamespace}, &ns); err != nil {
		t.Fatalf("shared namespace must survive single-tenant deletion: %v", err)
	}
	if !ns.DeletionTimestamp.IsZero() {
		t.Error("shared namespace was marked for deletion")
	}
}
