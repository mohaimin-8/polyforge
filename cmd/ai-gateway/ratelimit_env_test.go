package main

import (
	"testing"

	"polyforge/internal/ai/gateway"
)

// Final check-up (2026-10-13): an unparseable POLYFORGE_RATE_LIMIT_RPM (for
// example Helm's "1e+06") used to read as 0, which turned the gateway's
// limiter OFF without a word. A malformed value must now stop the process;
// unset or zero still means "no limit configured", as before.
func TestParseRateLimit(t *testing.T) {
	cases := []struct {
		rpm, burst string
		want       gateway.RateLimit
		wantErr    bool
	}{
		{"", "", gateway.RateLimit{}, false},
		{"0", "0", gateway.RateLimit{}, false},
		{"1200", "120", gateway.RateLimit{RequestsPerMinute: 1200, Burst: 120}, false},
		{"1e+06", "60", gateway.RateLimit{}, true},
		{"600", "lots", gateway.RateLimit{}, true},
		{"-1", "60", gateway.RateLimit{}, true},
	}
	for _, c := range cases {
		got, err := parseRateLimit(c.rpm, c.burst)
		if (err != nil) != c.wantErr || (!c.wantErr && got != c.want) {
			t.Errorf("parseRateLimit(%q, %q) = %+v, %v; want %+v, error=%v", c.rpm, c.burst, got, err, c.want, c.wantErr)
		}
	}
}
