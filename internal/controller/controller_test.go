package controller

import (
	"testing"

	"polyforge/internal/classifier"
)

func TestRecommendAICacheable(t *testing.T) {
	rec := Recommend(classifier.Result{TenantID: "acme", Label: classifier.AICacheable})
	if rec.CacheMB < 512 {
		t.Fatalf("expected cache-heavy recommendation, got %d MB", rec.CacheMB)
	}
	if rec.ModelTier == "none" {
		t.Fatal("expected AI model tier recommendation")
	}
}
