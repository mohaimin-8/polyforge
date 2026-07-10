package fairness

import (
	"context"
	"math"
	"testing"
)

func TestJainIndex(t *testing.T) {
	cases := []struct {
		name   string
		values []float64
		want   float64
	}{
		{"all equal", []float64{1, 1, 1, 1}, 1.0},
		{"one monopolist", []float64{1, 0, 0}, 1.0 / 3.0},
		{"empty is vacuously fair", nil, 1.0},
		{"all zero is vacuously fair", []float64{0, 0}, 1.0},
		{"mild skew", []float64{1.0, 0.8}, (1.8 * 1.8) / (2 * (1 + 0.64))},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := JainIndex(tc.values); math.Abs(got-tc.want) > 1e-9 {
				t.Errorf("JainIndex(%v) = %v, want %v", tc.values, got, tc.want)
			}
		})
	}
}

// clusterWindow is one observation window; noisy=true makes the first
// tenant an outlier on every signal, noisy=false makes it blend in
// (a mitigated tenant is quiet on all signals, not just CPU).
func clusterWindow(noisy bool) []Sample {
	first := Sample{TenantID: "noisy", CPUStallShare: 0.05, SyscallRate: 2_100, MemPressureEvents: 0}
	if noisy {
		first = Sample{TenantID: "noisy", CPUStallShare: 0.60, SyscallRate: 50_000, MemPressureEvents: 12}
	}
	return []Sample{
		first,
		{TenantID: "quiet-a", CPUStallShare: 0.05, SyscallRate: 2_000, MemPressureEvents: 0},
		{TenantID: "quiet-b", CPUStallShare: 0.06, SyscallRate: 2_500, MemPressureEvents: 1},
		{TenantID: "quiet-c", CPUStallShare: 0.04, SyscallRate: 1_800, MemPressureEvents: 0},
	}
}

func TestDetectorFlagsNoisyTenantWithinThreeWindows(t *testing.T) {
	d := NewDetector(DetectorConfig{})
	ctx := context.Background()

	for window := 1; window <= 3; window++ {
		d.Observe(clusterWindow(true))
		if window < 3 && d.TenantInterference(ctx, "noisy") != 0 {
			t.Errorf("flagged after %d windows; hysteresis requires 3", window)
		}
	}
	if got := d.TenantInterference(ctx, "noisy"); got <= 0 {
		t.Error("noisy tenant not flagged after 3 windows")
	}
	for _, quiet := range []string{"quiet-a", "quiet-b", "quiet-c"} {
		if got := d.TenantInterference(ctx, quiet); got != 0 {
			t.Errorf("%s flagged with score %v", quiet, got)
		}
	}
	if flagged := d.Flagged(); len(flagged) != 1 || flagged[0] != "noisy" {
		t.Errorf("flagged = %v", flagged)
	}
}

func TestDetectorUniformlyBusyClusterFlagsNobody(t *testing.T) {
	d := NewDetector(DetectorConfig{})
	// Everyone is equally loud: high absolute load, zero relative excess.
	window := []Sample{
		{TenantID: "a", CPUStallShare: 0.7, SyscallRate: 40_000, MemPressureEvents: 10},
		{TenantID: "b", CPUStallShare: 0.72, SyscallRate: 41_000, MemPressureEvents: 11},
		{TenantID: "c", CPUStallShare: 0.69, SyscallRate: 39_500, MemPressureEvents: 9},
	}
	for range 5 {
		d.Observe(window)
	}
	if flagged := d.Flagged(); len(flagged) != 0 {
		t.Errorf("uniform load flagged %v — noisy must mean louder than peers", flagged)
	}
}

func TestDetectorRecoversAfterMitigation(t *testing.T) {
	d := NewDetector(DetectorConfig{ClearWindows: 4})
	ctx := context.Background()
	for range 3 {
		d.Observe(clusterWindow(true))
	}
	if d.TenantInterference(ctx, "noisy") == 0 {
		t.Fatal("precondition: tenant should be flagged")
	}
	// Post-mitigation the tenant behaves; flag clears after ClearWindows.
	for window := 1; window <= 4; window++ {
		d.Observe(clusterWindow(false))
		if window < 4 && d.TenantInterference(ctx, "noisy") == 0 {
			t.Errorf("flag cleared after only %d clean windows", window)
		}
	}
	if got := d.TenantInterference(ctx, "noisy"); got != 0 {
		t.Errorf("flag not cleared after clean streak, score %v", got)
	}
}

func TestDetectorForgetsVanishedTenants(t *testing.T) {
	d := NewDetector(DetectorConfig{})
	for range 3 {
		d.Observe(clusterWindow(true))
	}
	d.Observe(clusterWindow(true)[1:]) // noisy tenant deleted
	if got := d.TenantInterference(context.Background(), "noisy"); got != 0 {
		t.Errorf("vanished tenant still scored %v", got)
	}
}
