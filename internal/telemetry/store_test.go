package telemetry

import (
	"context"
	"testing"
	"time"
)

func TestPercentileEdgeCases(t *testing.T) {
	hundred := make([]float64, 100)
	for i := range hundred {
		hundred[i] = float64(i + 1)
	}

	cases := []struct {
		name   string
		values []float64
		p      float64
		want   float64
	}{
		{"empty is zero", nil, 95, 0},
		{"single value", []float64{42}, 95, 42},
		{"pair rounds up to the top", []float64{10, 20}, 95, 20},
		{"unsorted input is sorted", []float64{30, 10, 20}, 50, 20},
		{"p95 of 1..100", hundred, 95, 95},
		{"p100 clamps to max", hundred, 100, 100},
		{"p0 clamps to min", hundred, 0, 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := percentile(tc.values, tc.p); got != tc.want {
				t.Fatalf("percentile(%v, %v) = %v, want %v", tc.values, tc.p, got, tc.want)
			}
		})
	}
}

func TestBuildFeatureSetWindowIsHalfOpen(t *testing.T) {
	since := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	until := since.Add(10 * time.Minute)
	events := []Event{
		{TenantID: "alpha", Service: "api", Timestamp: since.Add(-time.Nanosecond)},
		{TenantID: "alpha", Service: "api", Timestamp: since},
		{TenantID: "alpha", Service: "api", Timestamp: until.Add(-time.Nanosecond)},
		{TenantID: "alpha", Service: "api", Timestamp: until},
	}

	set := BuildFeatureSet("alpha", FeatureQuery{Since: since, Until: until}, events)
	if len(set.Items) != 1 {
		t.Fatalf("expected one service row, got %+v", set.Items)
	}
	if set.Items[0].EventCount != 2 {
		t.Fatalf("expected [since, until) to admit exactly 2 events, got %d", set.Items[0].EventCount)
	}
	if !set.Items[0].FirstEventTimestamp.Equal(since) {
		t.Fatalf("expected since to be inclusive, first event was %v", set.Items[0].FirstEventTimestamp)
	}
	if !set.Items[0].LastEventTimestamp.Before(until) {
		t.Fatalf("expected until to be exclusive, last event was %v", set.Items[0].LastEventTimestamp)
	}
}

func TestBuildFeatureSetGroupsAndLabelsUnknowns(t *testing.T) {
	since := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	until := since.Add(10 * time.Minute)
	at := since.Add(time.Minute)
	events := []Event{
		{TenantID: "alpha", Service: "", Timestamp: at, ModelTier: ""},
		{TenantID: "alpha", Service: "zeta", Timestamp: at, ModelTier: "mid"},
		{TenantID: "alpha", Service: "api", Timestamp: at, ModelTier: "mid"},
		{TenantID: "bravo", Service: "api", Timestamp: at, ModelTier: "large"},
	}

	set := BuildFeatureSet("alpha", FeatureQuery{Since: since, Until: until}, events)
	got := make([]string, 0, len(set.Items))
	for _, item := range set.Items {
		got = append(got, item.Service)
	}
	want := []string{"api", "unknown", "zeta"}
	if len(got) != len(want) {
		t.Fatalf("expected services %v, got %v", want, got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("expected services sorted as %v, got %v", want, got)
		}
	}
	for _, item := range set.Items {
		if item.Service == "unknown" && item.ModelTierCounts["unknown"] != 1 {
			t.Fatalf("expected blank model tier to bucket as unknown, got %v", item.ModelTierCounts)
		}
	}
}

func TestNormalizeFeatureQueryRepairsInvertedWindow(t *testing.T) {
	until := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	query := NormalizeFeatureQuery(FeatureQuery{Since: until.Add(time.Hour), Until: until})
	if !query.Since.Equal(until.Add(-DefaultFeatureLookback)) {
		t.Fatalf("expected inverted window to reset to the default lookback, got since=%v", query.Since)
	}

	defaulted := NormalizeFeatureQuery(FeatureQuery{})
	if !defaulted.Since.Before(defaulted.Until) {
		t.Fatalf("expected a valid default window, got since=%v until=%v", defaulted.Since, defaulted.Until)
	}
	if got := defaulted.Until.Sub(defaulted.Since); got != DefaultFeatureLookback {
		t.Fatalf("expected default lookback of %v, got %v", DefaultFeatureLookback, got)
	}
}

func TestClampFeatureEvents(t *testing.T) {
	atCap := make([]Event, MaxFeatureEvents)
	kept, truncated := ClampFeatureEvents(atCap)
	if truncated || len(kept) != MaxFeatureEvents {
		t.Fatalf("exactly MaxFeatureEvents must not report truncation, got truncated=%v len=%d", truncated, len(kept))
	}

	overCap := make([]Event, MaxFeatureEvents+1)
	overCap[0].Service = "first"
	kept, truncated = ClampFeatureEvents(overCap)
	if !truncated || len(kept) != MaxFeatureEvents {
		t.Fatalf("MaxFeatureEvents+1 must clamp and report truncation, got truncated=%v len=%d", truncated, len(kept))
	}
	if kept[0].Service != "first" {
		t.Fatal("clamp must keep the head of the slice, not the tail")
	}
}

func TestStoreFeaturesReportsTruncation(t *testing.T) {
	ctx := context.Background()
	store := NewStore(MaxFeatureEvents + 10)
	base := time.Now().UTC().Add(-5 * time.Minute)

	// The newest event is inserted first: if selection clamped in insertion
	// order instead of timestamp order, it would survive and the assertion on
	// LastEventTimestamp below would catch the drift from the SQL stores.
	newest := Event{TenantID: "alpha", Service: "api", Timestamp: base.Add(time.Duration(MaxFeatureEvents) * time.Millisecond)}
	if _, err := store.Add(ctx, newest); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < MaxFeatureEvents; i++ {
		event := Event{TenantID: "alpha", Service: "api", Timestamp: base.Add(time.Duration(i) * time.Millisecond)}
		if _, err := store.Add(ctx, event); err != nil {
			t.Fatal(err)
		}
	}

	set, err := store.Features(ctx, "alpha", FeatureQuery{})
	if err != nil {
		t.Fatal(err)
	}
	if !set.Truncated {
		t.Fatal("expected Truncated=true when the window holds MaxFeatureEvents+1 events")
	}
	if len(set.Items) != 1 || set.Items[0].EventCount != MaxFeatureEvents {
		t.Fatalf("expected exactly MaxFeatureEvents aggregated events, got %+v", set.Items)
	}
	wantLast := base.Add(time.Duration(MaxFeatureEvents-1) * time.Millisecond)
	if !set.Items[0].LastEventTimestamp.Equal(wantLast) {
		t.Fatalf("expected the earliest-by-timestamp prefix to survive (last=%v), got last=%v", wantLast, set.Items[0].LastEventTimestamp)
	}

	// A window that admits fewer than the cap must not report truncation.
	bounded, err := store.Features(ctx, "alpha", FeatureQuery{Since: base, Until: base.Add(time.Second)})
	if err != nil {
		t.Fatal(err)
	}
	if bounded.Truncated {
		t.Fatal("expected Truncated=false for a window under the cap")
	}
}

func TestStoreFeaturesRespectsTenantAndCapacity(t *testing.T) {
	ctx := context.Background()
	store := NewStore(10)
	at := time.Now().UTC().Add(-time.Minute)

	for _, event := range []Event{
		{TenantID: "alpha", Service: "api", Timestamp: at, LatencyMS: 10, CacheHit: true},
		{TenantID: "alpha", Service: "api", Timestamp: at, LatencyMS: 20},
		{TenantID: "bravo", Service: "api", Timestamp: at, LatencyMS: 999},
	} {
		if _, err := store.Add(ctx, event); err != nil {
			t.Fatal(err)
		}
	}

	set, err := store.Features(ctx, "alpha", FeatureQuery{})
	if err != nil {
		t.Fatal(err)
	}
	if len(set.Items) != 1 || set.Items[0].EventCount != 2 {
		t.Fatalf("expected only alpha events, got %+v", set.Items)
	}
	if set.Items[0].AvgLatencyMS != 15 || set.Items[0].CacheHitRate != 0.5 {
		t.Fatalf("unexpected aggregation: %+v", set.Items[0])
	}
}
