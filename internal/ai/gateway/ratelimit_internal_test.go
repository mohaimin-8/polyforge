package gateway

import (
	"testing"
	"time"
)

func TestTenantLimiterBucketRefills(t *testing.T) {
	base := time.Date(2026, 8, 8, 12, 0, 0, 0, time.UTC)
	now := base
	l := newTenantLimiter(RateLimit{RequestsPerMinute: 60, Burst: 5}) // 1 token/s, burst 5
	l.clock = func() time.Time { return now }

	// Drain the burst.
	for i := 0; i < 5; i++ {
		if ok, _ := l.allow("acme"); !ok {
			t.Fatalf("token %d denied inside burst", i)
		}
	}
	if ok, wait := l.allow("acme"); ok || wait <= 0 {
		t.Fatal("6th request in one instant should be denied with a positive wait")
	}
	// After one second, exactly one token is back.
	now = base.Add(time.Second)
	if ok, _ := l.allow("acme"); !ok {
		t.Fatal("one token should have refilled after 1s")
	}
	if ok, _ := l.allow("acme"); ok {
		t.Fatal("only one token should refill per second")
	}
}

func TestTenantLimiterIsolatesTenants(t *testing.T) {
	l := newTenantLimiter(RateLimit{RequestsPerMinute: 60, Burst: 1})
	if ok, _ := l.allow("acme"); !ok {
		t.Fatal("acme first request denied")
	}
	// acme is now empty, but a different tenant has its own full bucket.
	if ok, _ := l.allow("globex"); !ok {
		t.Fatal("globex must not be throttled by acme's usage")
	}
}

func TestTenantLimiterForgetReclaims(t *testing.T) {
	l := newTenantLimiter(RateLimit{RequestsPerMinute: 60, Burst: 1})
	l.allow("acme")
	if _, ok := l.buckets["acme"]; !ok {
		t.Fatal("bucket not created")
	}
	l.forget("acme")
	if _, ok := l.buckets["acme"]; ok {
		t.Fatal("forget did not drop the bucket")
	}
}

func TestNewTenantLimiterDisabled(t *testing.T) {
	if newTenantLimiter(RateLimit{RequestsPerMinute: 0, Burst: 10}) != nil {
		t.Error("zero RPM should disable the limiter")
	}
	if newTenantLimiter(RateLimit{RequestsPerMinute: 10, Burst: 0}) != nil {
		t.Error("zero burst should disable the limiter")
	}
}
