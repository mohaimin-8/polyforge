package controllers

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
)

func newPolicyClient(t *testing.T, objs ...client.Object) client.WithWatch {
	t.Helper()
	return fake.NewClientBuilder().
		WithScheme(newScheme(t)).
		WithObjects(objs...).
		WithStatusSubresource(&pfv1alpha1.Policy{}).
		Build()
}

func testDeployment(replicas int32) *appsv1.Deployment {
	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Namespace: SharedTenantNamespace, Name: "pf-gateway-acme"},
		Spec:       appsv1.DeploymentSpec{Replicas: &replicas},
	}
}

func testPolicy(spec pfv1alpha1.PolicySpec) *pfv1alpha1.Policy {
	if spec.TenantRef == "" {
		spec.TenantRef = "acme"
	}
	return &pfv1alpha1.Policy{
		ObjectMeta: metav1.ObjectMeta{Name: "acme"},
		Spec:       spec,
	}
}

func reconcilePolicy(t *testing.T, c client.Client) ctrl.Result {
	t.Helper()
	r := &PolicyReconciler{Client: c}
	res, err := r.Reconcile(context.Background(), ctrl.Request{
		NamespacedName: types.NamespacedName{Name: "acme"},
	})
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	return res
}

func TestPolicyReconcilePatchesDeploymentAndConfigMap(t *testing.T) {
	policy := testPolicy(pfv1alpha1.PolicySpec{
		Replicas:    5,
		ReplicaMin:  1,
		ReplicaMax:  10,
		CacheSizeMB: 512,
		ModelTier:   pfv1alpha1.ModelTierMid,
	})
	c := newPolicyClient(t, policy, testDeployment(2))
	reconcilePolicy(t, c)

	ctx := context.Background()
	var deploy appsv1.Deployment
	if err := c.Get(ctx, types.NamespacedName{Namespace: SharedTenantNamespace, Name: "pf-gateway-acme"}, &deploy); err != nil {
		t.Fatal(err)
	}
	if deploy.Spec.Replicas == nil || *deploy.Spec.Replicas != 5 {
		t.Errorf("deployment replicas = %v, want 5", deploy.Spec.Replicas)
	}

	var cm corev1.ConfigMap
	if err := c.Get(ctx, types.NamespacedName{Namespace: SharedTenantNamespace, Name: "pf-tenant-config-acme"}, &cm); err != nil {
		t.Fatalf("tenant configmap not created: %v", err)
	}
	if cm.Data["cache_size_mb"] != "512" || cm.Data["model_tier"] != "mid" {
		t.Errorf("configmap data = %v", cm.Data)
	}

	var got pfv1alpha1.Policy
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Status.AppliedReplicas != 5 || got.Status.AppliedCacheSizeMB != 512 {
		t.Errorf("status applied = replicas %d cache %d", got.Status.AppliedReplicas, got.Status.AppliedCacheSizeMB)
	}
	applied := meta.FindStatusCondition(got.Status.Conditions, ConditionApplied)
	if applied == nil || applied.Status != metav1.ConditionTrue {
		t.Errorf("Applied condition = %+v, want True", applied)
	}
}

func TestPolicyClampsReplicasToBounds(t *testing.T) {
	cases := []struct {
		name               string
		replicas, min, max int32
		want               int32
	}{
		{"below floor", 0, 2, 10, 2},
		{"above ceiling", 50, 1, 10, 10},
		{"inverted bounds favor floor", 2, 5, 3, 5},
		{"within bounds untouched", 4, 1, 10, 4},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			policy := testPolicy(pfv1alpha1.PolicySpec{
				Replicas:    tc.replicas,
				ReplicaMin:  tc.min,
				ReplicaMax:  tc.max,
				CacheSizeMB: 128,
				ModelTier:   pfv1alpha1.ModelTierSmall,
			})
			c := newPolicyClient(t, policy, testDeployment(1))
			reconcilePolicy(t, c)

			var deploy appsv1.Deployment
			if err := c.Get(context.Background(), types.NamespacedName{
				Namespace: SharedTenantNamespace, Name: "pf-gateway-acme",
			}, &deploy); err != nil {
				t.Fatal(err)
			}
			if deploy.Spec.Replicas == nil || *deploy.Spec.Replicas != tc.want {
				t.Errorf("replicas = %v, want %d", deploy.Spec.Replicas, tc.want)
			}
		})
	}
}

func TestPolicyMissingDeploymentRequeuesWithCondition(t *testing.T) {
	policy := testPolicy(pfv1alpha1.PolicySpec{
		Replicas:    2,
		ReplicaMin:  1,
		ReplicaMax:  10,
		CacheSizeMB: 128,
		ModelTier:   pfv1alpha1.ModelTierSmall,
	})
	c := newPolicyClient(t, policy)
	res := reconcilePolicy(t, c)

	if res.RequeueAfter != targetMissingRequeue {
		t.Errorf("requeue after = %v, want %v", res.RequeueAfter, targetMissingRequeue)
	}
	var got pfv1alpha1.Policy
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	applied := meta.FindStatusCondition(got.Status.Conditions, ConditionApplied)
	if applied == nil || applied.Status != metav1.ConditionFalse || applied.Reason != "TargetMissing" {
		t.Errorf("Applied condition = %+v, want False/TargetMissing", applied)
	}
}

func TestPolicyExplicitTargetDeployment(t *testing.T) {
	replicas := int32(1)
	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Namespace: "pf-tenant-acme", Name: "custom-gw"},
		Spec:       appsv1.DeploymentSpec{Replicas: &replicas},
	}
	policy := testPolicy(pfv1alpha1.PolicySpec{
		TargetDeployment: "pf-tenant-acme/custom-gw",
		Replicas:         3,
		ReplicaMin:       1,
		ReplicaMax:       10,
		CacheSizeMB:      128,
		ModelTier:        pfv1alpha1.ModelTierSmall,
	})
	c := newPolicyClient(t, policy, deploy)
	reconcilePolicy(t, c)

	var got appsv1.Deployment
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "pf-tenant-acme", Name: "custom-gw"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Spec.Replicas == nil || *got.Spec.Replicas != 3 {
		t.Errorf("replicas = %v, want 3", got.Spec.Replicas)
	}
}
