package telemetry

import (
	"sync"
	"testing"
	"time"
)

func TestRateTrackerReportsRateOverWindow(t *testing.T) {
	tr := NewRateTracker(10 * time.Second)
	base := time.Date(2026, 8, 8, 12, 0, 0, 0, time.UTC)
	tr.now = func() time.Time { return base }

	// 20 requests inside one 10 s window = 2 rps.
	var last float64
	for i := 0; i < 20; i++ {
		last = tr.Observe("acme|chat")
	}
	if last != 2.0 {
		t.Errorf("rate = %v, want 2.0", last)
	}
}

func TestRateTrackerNeverReportsZeroForLiveTraffic(t *testing.T) {
	// The bug being prevented: a tenant that is actively sending must never
	// report 0, because the planner reads 0 as "no demand" and freezes.
	tr := NewRateTracker(10 * time.Second)
	if got := tr.Observe("acme|chat"); got <= 0 {
		t.Errorf("first request reported %v, want > 0", got)
	}
}

func TestRateTrackerForgetsSamplesOutsideWindow(t *testing.T) {
	tr := NewRateTracker(10 * time.Second)
	base := time.Date(2026, 8, 8, 12, 0, 0, 0, time.UTC)
	now := base
	tr.now = func() time.Time { return now }

	for i := 0; i < 50; i++ {
		tr.Observe("acme|chat")
	}
	// Jump a full window past those samples; only the new one counts.
	now = base.Add(30 * time.Second)
	if got := tr.Observe("acme|chat"); got != 0.1 {
		t.Errorf("rate after idle = %v, want 0.1 (1 sample / 10 s)", got)
	}
}

func TestRateTrackerSeparatesKeys(t *testing.T) {
	tr := NewRateTracker(10 * time.Second)
	base := time.Date(2026, 8, 8, 12, 0, 0, 0, time.UTC)
	tr.now = func() time.Time { return base }

	for i := 0; i < 30; i++ {
		tr.Observe("acme|chat")
	}
	if got := tr.Observe("acme|crud_read"); got != 0.1 {
		t.Errorf("second key = %v, want 0.1 — keys must not share history", got)
	}
	if got := tr.Observe("other|chat"); got != 0.1 {
		t.Errorf("second tenant = %v, want 0.1", got)
	}
}

func TestRateTrackerIsConcurrencySafe(t *testing.T) {
	tr := NewRateTracker(time.Second)
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				tr.Observe("acme|chat")
			}
		}()
	}
	wg.Wait() // -race proves the locking
}

func TestRateTrackerNilIsSafe(t *testing.T) {
	var tr *RateTracker
	if got := tr.Observe("acme|chat"); got != 0 {
		t.Errorf("nil tracker = %v, want 0", got)
	}
	tr.Forget("acme|chat")
}
