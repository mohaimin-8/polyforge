package controller

import (
	"testing"

	"polyforge/internal/classifier"
)

func TestRecommendCoversEveryWorkloadClass(t *testing.T) {
	cases := []struct {
		label        classifier.Label
		replicas     int
		cacheMB      int
		modelTier    string
		fairnessMode string
	}{
		{classifier.CRUDBursty, 5, 128, "none", "shared"},
		{classifier.CRUDSteady, 2, 96, "none", "shared"},
		{classifier.AICacheable, 3, 1024, "mid", "shared"},
		{classifier.AIUncacheable, 4, 256, "large", "shared"},
		{classifier.AgenticMultistep, 5, 512, "mid", "guarded"},
		{classifier.Unknown, 2, 128, "small", "shared"},
	}
	for _, tc := range cases {
		t.Run(string(tc.label), func(t *testing.T) {
			rec := Recommend(classifier.Result{TenantID: "acme", Label: tc.label})
			if rec.TenantID != "acme" || rec.Workload != tc.label {
				t.Fatalf("identity fields not propagated: %+v", rec)
			}
			if rec.Replicas != tc.replicas || rec.CacheMB != tc.cacheMB {
				t.Fatalf("%s: expected replicas=%d cache=%d, got %+v", tc.label, tc.replicas, tc.cacheMB, rec)
			}
			if rec.ModelTier != tc.modelTier || rec.FairnessMode != tc.fairnessMode {
				t.Fatalf("%s: expected tier=%q fairness=%q, got %+v", tc.label, tc.modelTier, tc.fairnessMode, rec)
			}
			if rec.Reason == "" {
				t.Fatalf("%s: every recommendation must be explainable", tc.label)
			}
		})
	}
}

// The policy invariants below encode why the action vector is shaped the way
// it is; a future controller iteration must not silently regress them.
func TestRecommendPolicyInvariants(t *testing.T) {
	crud := Recommend(classifier.Result{Label: classifier.CRUDSteady})
	bursty := Recommend(classifier.Result{Label: classifier.CRUDBursty})
	cacheable := Recommend(classifier.Result{Label: classifier.AICacheable})
	uncacheable := Recommend(classifier.Result{Label: classifier.AIUncacheable})
	agentic := Recommend(classifier.Result{Label: classifier.AgenticMultistep})

	if bursty.Replicas <= crud.Replicas {
		t.Fatal("bursty CRUD must receive more replica headroom than steady CRUD")
	}
	if cacheable.CacheMB <= uncacheable.CacheMB {
		t.Fatal("cacheable AI must receive a larger cache budget than uncacheable AI")
	}
	if crud.ModelTier != "none" || bursty.ModelTier != "none" {
		t.Fatal("CRUD workloads must not be routed to any model tier")
	}
	if agentic.FairnessMode != "guarded" {
		t.Fatal("agentic fan-out must trigger neighbor isolation")
	}
}

func TestRecommendUnknownLabelFallsBackConservatively(t *testing.T) {
	rec := Recommend(classifier.Result{TenantID: "acme", Label: classifier.Label("never-seen")})
	if rec.Replicas != 2 || rec.CacheMB != 128 || rec.ModelTier != "small" {
		t.Fatalf("unrecognized label must use conservative defaults, got %+v", rec)
	}
	if rec.Reason == "" {
		t.Fatal("fallback must still be explainable")
	}
}
