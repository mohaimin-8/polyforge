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

// The cache and tier knobs clamp to their bounds at the single actuation
// point, exactly like replicas — so a pinned (min==max) knob is physically
// frozen at the data plane. This is the mechanism the cache-only / tier-only
// live ablations (PREREG_WAVE4_LIVE_PLANE.md §Arms) rely on to freeze two of
// the three knobs while the operator arm still runs.
func TestPolicyClampsCacheToBounds(t *testing.T) {
	cases := []struct {
		name            string
		cache, min, max int32
		want            int32
	}{
		{"below floor", 64, 256, 1024, 256},
		{"above ceiling", 4096, 0, 512, 512},
		{"pinned min==max freezes", 4096, 128, 128, 128},
		{"zero max means no ceiling", 900, 0, 0, 900},
		{"within bounds untouched", 512, 128, 1024, 512},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			spec := &pfv1alpha1.PolicySpec{
				CacheSizeMB:    tc.cache,
				CacheSizeMBMin: tc.min,
				CacheSizeMBMax: tc.max,
			}
			if got := clampCache(spec); got != tc.want {
				t.Errorf("clampCache = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestPolicyClampsTierToBounds(t *testing.T) {
	cases := []struct {
		name                 string
		tier, min, max, want pfv1alpha1.ModelTier
	}{
		{"below floor", pfv1alpha1.ModelTierNone, pfv1alpha1.ModelTierMid, pfv1alpha1.ModelTierLarge, pfv1alpha1.ModelTierMid},
		{"above ceiling", pfv1alpha1.ModelTierLarge, pfv1alpha1.ModelTierNone, pfv1alpha1.ModelTierSmall, pfv1alpha1.ModelTierSmall},
		{"pinned min==max freezes", pfv1alpha1.ModelTierLarge, pfv1alpha1.ModelTierSmall, pfv1alpha1.ModelTierSmall, pfv1alpha1.ModelTierSmall},
		{"empty bounds mean no bound", pfv1alpha1.ModelTierMid, "", "", pfv1alpha1.ModelTierMid},
		{"within bounds untouched", pfv1alpha1.ModelTierMid, pfv1alpha1.ModelTierSmall, pfv1alpha1.ModelTierLarge, pfv1alpha1.ModelTierMid},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			spec := &pfv1alpha1.PolicySpec{
				ModelTier:    tc.tier,
				ModelTierMin: tc.min,
				ModelTierMax: tc.max,
			}
			if got := clampTier(spec); got != tc.want {
				t.Errorf("clampTier = %q, want %q", got, tc.want)
			}
		})
	}
}

// A pinned Policy: the operator writes the frozen cache/tier to the gateway
// ConfigMap even though the spec's setpoints (as a planner would write them)
// are outside the pins. This is the end-to-end cache-only/tier-only posture.
func TestPolicyPinnedKnobsFreezeConfigMap(t *testing.T) {
	policy := testPolicy(pfv1alpha1.PolicySpec{
		Replicas:       5,
		ReplicaMin:     1,
		ReplicaMax:     10,
		CacheSizeMB:    2048, // planner "wants" 2048...
		CacheSizeMBMin: 128,  // ...but the cache knob is pinned at 128
		CacheSizeMBMax: 128,
		ModelTier:      pfv1alpha1.ModelTierLarge, // planner "wants" large...
		ModelTierMin:   pfv1alpha1.ModelTierSmall, // ...but the tier is pinned small
		ModelTierMax:   pfv1alpha1.ModelTierSmall,
	})
	c := newPolicyClient(t, policy, testDeployment(2))
	reconcilePolicy(t, c)

	var cm corev1.ConfigMap
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: SharedTenantNamespace, Name: "pf-tenant-config-acme"}, &cm); err != nil {
		t.Fatalf("tenant configmap not created: %v", err)
	}
	if cm.Data["cache_size_mb"] != "128" || cm.Data["model_tier"] != "small" {
		t.Errorf("pinned knobs leaked: configmap = %v, want cache 128 / tier small", cm.Data)
	}
	var got pfv1alpha1.Policy
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Status.AppliedCacheSizeMB != 128 || got.Status.AppliedModelTier != pfv1alpha1.ModelTierSmall {
		t.Errorf("status applied = cache %d tier %s, want 128/small", got.Status.AppliedCacheSizeMB, got.Status.AppliedModelTier)
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

// Shared-target aggregation: pool tenants whose Policies name the same
// Deployment each contribute their clamped share; the Deployment gets the
// sum. Last-writer-wins would pin a shared data plane at one tenant's
// share — the exact failure the Phase 7 live jcac arm must not have.
func TestPolicySharedTargetSumsContributions(t *testing.T) {
	replicas := int32(1)
	shared := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Namespace: "polyforge", Name: "polyforge-control-plane"},
		Spec:       appsv1.DeploymentSpec{Replicas: &replicas},
	}
	mk := func(name string, want, max int32) *pfv1alpha1.Policy {
		return &pfv1alpha1.Policy{
			ObjectMeta: metav1.ObjectMeta{Name: name},
			Spec: pfv1alpha1.PolicySpec{
				TenantRef:        name,
				TargetDeployment: "polyforge/polyforge-control-plane",
				Replicas:         want,
				ReplicaMin:       1,
				ReplicaMax:       max,
				CacheSizeMB:      128,
				ModelTier:        pfv1alpha1.ModelTierSmall,
			},
		}
	}
	// bravo asks for 50 but is clamped to 6: the sum must use clamped shares.
	c := newPolicyClient(t, mk("acme", 3, 6), mk("bravo", 50, 6), shared)
	r := &PolicyReconciler{Client: c}
	ctx := context.Background()
	if _, err := r.Reconcile(ctx, ctrl.Request{NamespacedName: types.NamespacedName{Name: "acme"}}); err != nil {
		t.Fatalf("reconcile acme: %v", err)
	}

	var deploy appsv1.Deployment
	if err := c.Get(ctx, types.NamespacedName{Namespace: "polyforge", Name: "polyforge-control-plane"}, &deploy); err != nil {
		t.Fatal(err)
	}
	if deploy.Spec.Replicas == nil || *deploy.Spec.Replicas != 9 {
		t.Errorf("shared deployment replicas = %v, want 9 (3 + clamp(50)=6)", deploy.Spec.Replicas)
	}

	var acme pfv1alpha1.Policy
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &acme); err != nil {
		t.Fatal(err)
	}
	if acme.Status.AppliedReplicas != 3 {
		t.Errorf("acme applied replicas = %d, want its own share 3", acme.Status.AppliedReplicas)
	}

	// Removing a contributor shrinks the sum on the next sibling reconcile.
	if err := c.Delete(ctx, &pfv1alpha1.Policy{ObjectMeta: metav1.ObjectMeta{Name: "bravo"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Reconcile(ctx, ctrl.Request{NamespacedName: types.NamespacedName{Name: "acme"}}); err != nil {
		t.Fatalf("reconcile after delete: %v", err)
	}
	if err := c.Get(ctx, types.NamespacedName{Namespace: "polyforge", Name: "polyforge-control-plane"}, &deploy); err != nil {
		t.Fatal(err)
	}
	if deploy.Spec.Replicas == nil || *deploy.Spec.Replicas != 3 {
		t.Errorf("after delete replicas = %v, want 3", deploy.Spec.Replicas)
	}
}

// The map function must fan a Policy event out to the siblings sharing its
// target (so a deleted contributor still triggers the sum recompute) and
// nothing else.
func TestPoliciesSharingTargetEnqueuesSiblingsOnly(t *testing.T) {
	shared := func(name string) *pfv1alpha1.Policy {
		return &pfv1alpha1.Policy{
			ObjectMeta: metav1.ObjectMeta{Name: name},
			Spec: pfv1alpha1.PolicySpec{
				TenantRef:        name,
				TargetDeployment: "polyforge/polyforge-control-plane",
				Replicas:         2, ReplicaMin: 1, ReplicaMax: 6,
				CacheSizeMB: 128, ModelTier: pfv1alpha1.ModelTierSmall,
			},
		}
	}
	other := &pfv1alpha1.Policy{
		ObjectMeta: metav1.ObjectMeta{Name: "loner"},
		Spec: pfv1alpha1.PolicySpec{
			TenantRef: "loner", // default target: its own gateway
			Replicas:  2, ReplicaMin: 1, ReplicaMax: 6,
			CacheSizeMB: 128, ModelTier: pfv1alpha1.ModelTierSmall,
		},
	}
	a, b := shared("acme"), shared("bravo")
	c := newPolicyClient(t, a, b, other)
	r := &PolicyReconciler{Client: c}

	reqs := r.policiesSharingTarget(context.Background(), a)
	if len(reqs) != 1 || reqs[0].Name != "bravo" {
		t.Errorf("requests = %v, want exactly [bravo]", reqs)
	}
	if got := r.policiesSharingTarget(context.Background(), other); len(got) != 0 {
		t.Errorf("loner requests = %v, want none", got)
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
