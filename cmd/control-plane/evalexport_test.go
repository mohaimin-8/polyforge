package main

import (
	"testing"
	"time"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

func TestComputeEvalExportAggregatesHarnessSchema(t *testing.T) {
	base := time.Date(2026, 7, 12, 12, 0, 0, 0, time.UTC)
	tenants := []tenant.Tenant{
		{ID: "alpha", Plan: "standard"},   // SLO scale 2.5: crud 375ms, ai 6250ms
		{ID: "bravo", Plan: "premium"},    // SLO scale 1.0: crud 150ms, ai 2500ms
		{ID: "charlie", Plan: "standard"}, // no traffic: must not enter Jain
	}
	events := map[string][]telemetry.Event{
		"alpha": {
			// CRUD within target, one over target (400 > 375), same 10s step.
			{Timestamp: base, LatencyMS: 100},
			{Timestamp: base.Add(time.Second), LatencyMS: 400},
			// AI hit under target, AI miss over target (7000 > 6250), next step.
			{Timestamp: base.Add(11 * time.Second), LatencyMS: 30, CacheHit: true, ModelTier: "small"},
			{Timestamp: base.Add(12 * time.Second), LatencyMS: 7000, ModelTier: "mid"},
		},
		"bravo": {
			// Premium AI request within its 2500ms target, third step.
			{Timestamp: base.Add(21 * time.Second), LatencyMS: 2000, ModelTier: "large"},
		},
	}

	doc := computeEvalExport(tenants, events, 1.5)

	if doc.NEvents != 5 || doc.NTenants != 3 {
		t.Fatalf("expected 5 events over 3 tenants, got %d/%d", doc.NEvents, doc.NTenants)
	}
	// 2 of 5 requests exceeded their target.
	if doc.MeanViolation != 0.4 {
		t.Fatalf("expected mean_violation 0.4, got %v", doc.MeanViolation)
	}
	// Every one of the 3 steps has >5% of its requests over target except the
	// bravo step: steps 1 and 2 violate, step 3 does not.
	if doc.ViolationStepShare < 0.66 || doc.ViolationStepShare > 0.67 {
		t.Fatalf("expected violation_step_share 2/3, got %v", doc.ViolationStepShare)
	}
	// 1 cache hit among 3 AI requests.
	if doc.CacheHitRate < 0.333 || doc.CacheHitRate > 0.334 {
		t.Fatalf("expected cache_hit_rate 1/3, got %v", doc.CacheHitRate)
	}
	// p95 of [100, 400] = 400; p95 of [30, 7000, 2000] = 7000.
	if doc.CrudP95MS != 400 || doc.AIP95MS != 7000 {
		t.Fatalf("expected p95s 400/7000, got %v/%v", doc.CrudP95MS, doc.AIP95MS)
	}
	// p99 (Wave 3, live-only) reads the same latencies: ceil(0.99*n)-1.
	// [100,400] -> index 1 = 400; [30,2000,7000] -> index 2 = 7000.
	if doc.CrudP99MS != 400 || doc.AIP99MS != 7000 {
		t.Fatalf("expected p99s 400/7000, got %v/%v", doc.CrudP99MS, doc.AIP99MS)
	}
	// Tier cost: mid + large = 0.001 + 0.01. The small-tier event is a cache
	// hit — it never touched a backend, so it is charged nothing and appears
	// in no tier's serving histogram (the B1 cache knob's economy).
	if diff := doc.CostTierUSD - 0.011; diff > 1e-9 || diff < -1e-9 {
		t.Fatalf("expected tier cost 0.011, got %v", doc.CostTierUSD)
	}
	if doc.TierRequests["mid"] != 1 || doc.TierRequests["large"] != 1 || doc.TierRequests["small"] != 0 {
		t.Fatalf("expected tier histogram mid=1 large=1 small=0, got %v", doc.TierRequests)
	}
	if doc.CostInfraUSD != 1.5 || doc.TotalCostUSD != doc.CostTierUSD+1.5 {
		t.Fatalf("expected injected infra cost to sum into total, got %+v", doc)
	}
	// Jain over attainments [0.5, 1.0] (charlie has no traffic):
	// (1.5)^2 / (2 * 1.25) = 0.9.
	if diff := doc.MeanJain - 0.9; diff > 1e-9 || diff < -1e-9 {
		t.Fatalf("expected mean_jain 0.9, got %v", doc.MeanJain)
	}
}

func TestComputeEvalExportEmptyStoreIsWellFormed(t *testing.T) {
	doc := computeEvalExport(nil, nil, 0)
	if doc.NEvents != 0 || doc.MeanViolation != 0 || doc.TotalCostUSD != 0 {
		t.Fatalf("expected zeroed metrics on an empty store, got %+v", doc)
	}
	if doc.MeanJain != 1.0 {
		t.Fatalf("expected Jain 1.0 with no tenants, got %v", doc.MeanJain)
	}
}
