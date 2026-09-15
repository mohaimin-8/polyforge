package main

import (
	"bytes"
	"encoding/json"
	"math"
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

	doc, err := computeEvalExport(tenants, events, 1.5, 0)
	if err != nil {
		t.Fatalf("computeEvalExport: %v", err)
	}

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

// An unrecognised plan must fail the export, not silently score the tenant
// at "standard". The harness previously created live tenants with no plan
// field at all, so every tenant in every mix was graded at the 2.5x standard
// scale — premium 2.5x too leniently, best-effort 3.2x too strictly — which
// voids any live/sim parity reading on a non-uniform mix.
func TestComputeEvalExportRejectsUnknownPlan(t *testing.T) {
	for _, plan := range []string{"", "gold", "Premium"} {
		tenants := []tenant.Tenant{{ID: "alpha", Plan: plan}}
		if _, err := computeEvalExport(tenants, nil, 0, 0); err == nil {
			t.Errorf("plan %q was accepted; want an error rather than a "+
				"silent default to standard", plan)
		}
	}
	// The three real classes are accepted.
	for _, plan := range []string{"premium", "standard", "best-effort"} {
		tenants := []tenant.Tenant{{ID: "alpha", Plan: plan}}
		if _, err := computeEvalExport(tenants, nil, 0, 0); err != nil {
			t.Errorf("plan %q rejected: %v", plan, err)
		}
	}
}

func TestComputeEvalExportEmptyStoreIsWellFormed(t *testing.T) {
	doc, err := computeEvalExport(nil, nil, 0, 0)
	if err != nil {
		t.Fatalf("computeEvalExport: %v", err)
	}
	if doc.NEvents != 0 || doc.MeanViolation != 0 || doc.TotalCostUSD != 0 {
		t.Fatalf("expected zeroed metrics on an empty store, got %+v", doc)
	}
	if doc.MeanJain != 1.0 {
		t.Fatalf("expected Jain 1.0 with no tenants, got %v", doc.MeanJain)
	}
}

// TestComputeEvalExportBuckets pins the time-resolved output WP14 needed and
// did not have. SK-H3 is frozen on hour-bucketed crud_p95 and SK-H1 on how
// violation moves around a fault; before this, eval-export emitted only
// whole-run scalars, so four consecutive 24 h sittings produced one row of
// numbers between them and attempt 4's buckets had to be rebuilt from Postgres
// by hand before teardown destroyed the table.
func TestComputeEvalExportBuckets(t *testing.T) {
	base := time.Date(2026, 7, 12, 12, 0, 0, 0, time.UTC)
	tenants := []tenant.Tenant{{ID: "alpha", Plan: "standard"}} // crud target 375ms
	events := map[string][]telemetry.Event{
		"alpha": {
			// Bucket 1 (12:00-12:01): all fast, nothing over target.
			{Timestamp: base, LatencyMS: 10},
			{Timestamp: base.Add(10 * time.Second), LatencyMS: 20},
			{Timestamp: base.Add(20 * time.Second), LatencyMS: 30},
			// Bucket 2 (12:01-12:02): degraded, both over the 375ms target.
			{Timestamp: base.Add(70 * time.Second), LatencyMS: 400},
			{Timestamp: base.Add(80 * time.Second), LatencyMS: 500},
		},
	}

	doc, err := computeEvalExport(tenants, events, 0, 60)
	if err != nil {
		t.Fatalf("computeEvalExport: %v", err)
	}
	if len(doc.Buckets) != 2 {
		t.Fatalf("expected 2 one-minute buckets, got %d", len(doc.Buckets))
	}

	// Buckets are absolute epoch windows, so boundaries land on wall-clock
	// minutes regardless of when the run started, and they arrive sorted.
	if got := doc.Buckets[0].BucketStartUTC; got != "2026-07-12T12:00:00Z" {
		t.Fatalf("first bucket start = %q", got)
	}
	if got := doc.Buckets[1].BucketStartUTC; got != "2026-07-12T12:01:00Z" {
		t.Fatalf("second bucket start = %q", got)
	}

	if doc.Buckets[0].NEvents != 3 || doc.Buckets[1].NEvents != 2 {
		t.Fatalf("bucket sizes = %d/%d, want 3/2",
			doc.Buckets[0].NEvents, doc.Buckets[1].NEvents)
	}

	// The whole point: the degradation is visible per window. A run-level
	// scalar would average these into 0.4 and hide which minute went bad.
	if doc.Buckets[0].MeanViolation != 0 {
		t.Fatalf("healthy bucket violation = %v, want 0", doc.Buckets[0].MeanViolation)
	}
	if doc.Buckets[1].MeanViolation != 1 {
		t.Fatalf("degraded bucket violation = %v, want 1", doc.Buckets[1].MeanViolation)
	}

	// Percentiles must be the SAME nearest-rank order statistic as the
	// scalars beside them. ceil(0.95*3)-1 = index 2 -> 30; ceil(0.95*2)-1 = 1
	// -> 500. Interpolation would give 28 and 495, which is exactly the
	// mismatch that made the hand-rolled Postgres rescue use percentile_disc.
	if doc.Buckets[0].CrudP95MS != 30 {
		t.Fatalf("bucket 1 crud p95 = %v, want 30", doc.Buckets[0].CrudP95MS)
	}
	if doc.Buckets[1].CrudP95MS != 500 {
		t.Fatalf("bucket 2 crud p95 = %v, want 500", doc.Buckets[1].CrudP95MS)
	}

	// Run-level scalars must be untouched by bucketing.
	if doc.NEvents != 5 || doc.MeanViolation != 0.4 {
		t.Fatalf("scalars drifted: n=%d violation=%v", doc.NEvents, doc.MeanViolation)
	}
}

// TestComputeEvalExportBucketsOffByDefault protects every committed record:
// with --bucket-seconds unset the field is omitted entirely, so existing
// consumers and the byte-identity reproduction gate see no change at all.
func TestComputeEvalExportBucketsOffByDefault(t *testing.T) {
	base := time.Date(2026, 7, 12, 12, 0, 0, 0, time.UTC)
	tenants := []tenant.Tenant{{ID: "alpha", Plan: "standard"}}
	events := map[string][]telemetry.Event{
		"alpha": {{Timestamp: base, LatencyMS: 10}},
	}

	doc, err := computeEvalExport(tenants, events, 0, 0)
	if err != nil {
		t.Fatalf("computeEvalExport: %v", err)
	}
	if doc.Buckets != nil {
		t.Fatalf("buckets must be nil when disabled, got %d", len(doc.Buckets))
	}
	blob, err := json.Marshal(doc)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if bytes.Contains(blob, []byte("buckets")) {
		t.Fatalf("disabled buckets must not appear in the JSON at all:\n%s", blob)
	}
}

// Session 48: the Wave 4 pre-registration's paired bootstrap needs a cost
// per bucket; the export carried only the total. Bucket tier costs must sum
// to the document's tier cost, and cache hits must price nothing anywhere.
func TestComputeEvalExportBucketTierCostSumsToTotal(t *testing.T) {
	base := time.Date(2026, 7, 12, 12, 0, 0, 0, time.UTC)
	tenants := []tenant.Tenant{{ID: "t1", Plan: "standard"}}
	events := map[string][]telemetry.Event{"t1": {
		{Service: "chat", LatencyMS: 300, Timestamp: base, ModelTier: "small", CacheHit: false},
		{Service: "chat", LatencyMS: 5, Timestamp: base.Add(10 * time.Second), ModelTier: "small", CacheHit: true},
		{Service: "chat", LatencyMS: 900, Timestamp: base.Add(70 * time.Second), ModelTier: "mid", CacheHit: false},
		{Service: "crud_read", LatencyMS: 2, Timestamp: base.Add(75 * time.Second)},
	}}
	doc, err := computeEvalExport(tenants, events, 0, 60)
	if err != nil {
		t.Fatal(err)
	}
	if len(doc.Buckets) != 2 {
		t.Fatalf("buckets = %d, want 2", len(doc.Buckets))
	}
	var sum float64
	for _, b := range doc.Buckets {
		sum += b.CostTierUSD
	}
	if math.Abs(sum-doc.CostTierUSD) > 1e-12 || doc.CostTierUSD <= 0 {
		t.Fatalf("bucket tier costs %v do not sum to the document's %v", sum, doc.CostTierUSD)
	}
	if doc.Buckets[0].TierRequests["small"] != 1 || doc.Buckets[0].TierRequests["mid"] != 0 {
		t.Fatalf("bucket 0 tier requests = %v, want small:1 only (the hit prices nothing)", doc.Buckets[0].TierRequests)
	}
	if doc.Buckets[1].TierRequests["mid"] != 1 {
		t.Fatalf("bucket 1 tier requests = %v, want mid:1", doc.Buckets[1].TierRequests)
	}
}
