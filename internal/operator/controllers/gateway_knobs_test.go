package controllers

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
)

func TestHTTPGatewayKnobsPush(t *testing.T) {
	var gotPath, gotKey string
	var gotBody map[string]any
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotKey = r.Header.Get("X-PolyForge-Admin-Key")
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(ts.Close)

	pusher := NewHTTPGatewayKnobs(ts.URL, "adm")
	if err := pusher.Push(context.Background(), "acme", "mid", 512); err != nil {
		t.Fatalf("push: %v", err)
	}
	if gotPath != "/admin/tenants/acme/knobs" || gotKey != "adm" {
		t.Fatalf("request path=%q key=%q", gotPath, gotKey)
	}
	if gotBody["model_tier"] != "mid" || gotBody["cache_size_mb"] != float64(512) {
		t.Fatalf("body = %v", gotBody)
	}
}

func TestHTTPGatewayKnobsPushSurfacesGatewayError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, `{"error":{"code":"invalid_request"}}`, http.StatusBadRequest)
	}))
	t.Cleanup(ts.Close)
	if err := NewHTTPGatewayKnobs(ts.URL, "adm").Push(context.Background(), "acme", "huge", 1); err == nil {
		t.Fatal("a non-200 from the gateway must be an error")
	}
}

type failingPusher struct{}

func (failingPusher) Push(context.Context, string, string, int32) error {
	return errors.New("gateway unreachable")
}

// A failed knob push must keep Applied false: the harness's actuation gate
// (`kubectl wait --for=condition=Applied`) has to cover the cache/tier
// knobs, or a live run could score an inert-knob plan as actuated.
func TestPolicyReconcileFailedKnobPushKeepsAppliedFalse(t *testing.T) {
	policy := testPolicy(pfv1alpha1.PolicySpec{
		Replicas:    3,
		ReplicaMin:  1,
		ReplicaMax:  10,
		CacheSizeMB: 256,
		ModelTier:   pfv1alpha1.ModelTierSmall,
	})
	c := newPolicyClient(t, policy, testDeployment(2))
	r := &PolicyReconciler{Client: c, GatewayKnobs: failingPusher{}}
	res, err := r.Reconcile(context.Background(), ctrl.Request{
		NamespacedName: types.NamespacedName{Name: "acme"},
	})
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if res.RequeueAfter <= 0 {
		t.Fatalf("expected a requeue after a failed push, got %+v", res)
	}
	var got pfv1alpha1.Policy
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	applied := meta.FindStatusCondition(got.Status.Conditions, ConditionApplied)
	if applied == nil || applied.Status != metav1.ConditionFalse || applied.Reason != "GatewayKnobsFailed" {
		t.Fatalf("Applied condition = %+v, want False/GatewayKnobsFailed", applied)
	}
}

type recordingPusher struct {
	tenant string
	tier   string
	cache  int32
}

func (p *recordingPusher) Push(_ context.Context, tenantID, tier string, cacheMB int32) error {
	p.tenant, p.tier, p.cache = tenantID, tier, cacheMB
	return nil
}

func TestPolicyReconcilePushesKnobsAndApplies(t *testing.T) {
	policy := testPolicy(pfv1alpha1.PolicySpec{
		Replicas:    3,
		ReplicaMin:  1,
		ReplicaMax:  10,
		CacheSizeMB: 256,
		ModelTier:   pfv1alpha1.ModelTierSmall,
	})
	c := newPolicyClient(t, policy, testDeployment(2))
	pusher := &recordingPusher{}
	r := &PolicyReconciler{Client: c, GatewayKnobs: pusher}
	if _, err := r.Reconcile(context.Background(), ctrl.Request{
		NamespacedName: types.NamespacedName{Name: "acme"},
	}); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if pusher.tenant != "acme" || pusher.tier != "small" || pusher.cache != 256 {
		t.Fatalf("pushed knobs = %+v", pusher)
	}
	var got pfv1alpha1.Policy
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	applied := meta.FindStatusCondition(got.Status.Conditions, ConditionApplied)
	if applied == nil || applied.Status != metav1.ConditionTrue {
		t.Fatalf("Applied condition = %+v, want True after a successful push", applied)
	}
}
