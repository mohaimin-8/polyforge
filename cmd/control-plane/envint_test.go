package main

import "testing"

// Final check-up (2026-10-13): Helm renders a large number from a values
// file as "1e+06", and envInt used to fall back to the default on any parse
// error, so a 1,000,000 RPM limit silently became 600 -- the limiter behind
// the sessions 45-48 429s. A malformed value must now stop the process.
func TestParseEnvInt(t *testing.T) {
	cases := []struct {
		raw      string
		fallback int
		want     int
		wantErr  bool
	}{
		{"", 600, 600, false}, // unset keeps the default
		{"1000000", 600, 1000000, false},
		{"0", 600, 0, false},    // zero is meaningful: no budget configured
		{"1e+06", 600, 0, true}, // Helm's float rendering
		{"60/min", 600, 0, true},
		{"-5", 600, 0, true}, // a negative budget or interval is never valid
	}
	for _, c := range cases {
		got, err := parseEnvInt(c.raw, c.fallback)
		if (err != nil) != c.wantErr || (!c.wantErr && got != c.want) {
			t.Errorf("parseEnvInt(%q, %d) = %d, %v; want %d, error=%v", c.raw, c.fallback, got, err, c.want, c.wantErr)
		}
	}
}
