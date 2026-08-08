package telemetry

import (
	"sync"
	"time"
)

// RateWindow is the observation window for Event.RPSWindow. It matches the
// operator's default control interval: the planner's demand signal should
// describe the interval it is about to plan for, not a long historical
// average that lags the burst it is supposed to anticipate.
const RateWindow = 10 * time.Second

// maxRateSamples bounds per-key memory. At 10 s a key would need >400 rps to
// reach it; beyond that the rate is computed from the newest samples, which
// biases the estimate low rather than growing without limit.
const maxRateSamples = 4096

// idlePruneInterval bounds how often the map is swept for idle keys, so the
// sweep cost is amortised across many Observe calls.
const idlePruneInterval = 10 * time.Minute

// RateTracker measures per-key request rate over a sliding window, in
// process, with no external dependency.
//
// It exists because Event.RPSWindow was never populated by any production
// emitter — only by the classifier's synthetic fixtures. The planner sums
// that field into its demand estimate (operator/planner/demand.go), so an
// unset field meant every tenant reported zero demand and the joint
// controller held its initial configuration forever. A live campaign
// recorded exactly that: identical cost across repetitions, replicas pinned
// at their starting value, while the reactive baseline moved.
type RateTracker struct {
	mu        sync.Mutex
	window    time.Duration
	seen      map[string][]time.Time
	now       func() time.Time
	lastPrune time.Time
}

func NewRateTracker(window time.Duration) *RateTracker {
	if window <= 0 {
		window = RateWindow
	}
	return &RateTracker{
		window: window,
		seen:   make(map[string][]time.Time),
		now:    time.Now,
	}
}

// Observe records one request for key and returns the resulting rate in
// requests per second over the window, including this request. The first
// request in an idle window therefore reports 1/window rather than 0, so a
// tenant that has just started sending traffic is never reported as silent.
func (t *RateTracker) Observe(key string) float64 {
	if t == nil {
		return 0
	}
	now := t.now()
	cutoff := now.Add(-t.window)

	t.mu.Lock()
	defer t.mu.Unlock()

	samples := append(t.seen[key], now)
	// Samples are appended in time order, so the first index at or after the
	// cutoff bounds the live suffix.
	keep := 0
	for keep < len(samples) && samples[keep].Before(cutoff) {
		keep++
	}
	samples = samples[keep:]
	if len(samples) > maxRateSamples {
		samples = samples[len(samples)-maxRateSamples:]
	}
	t.seen[key] = samples

	t.pruneIdle(now)
	return float64(len(samples)) / t.window.Seconds()
}

// pruneIdle drops keys whose newest sample is older than the window, so the
// map is bounded by the number of *currently active* keys rather than by
// every key ever seen for the process lifetime. Amortised: it runs at most
// once per idlePruneInterval. Caller holds the lock.
func (t *RateTracker) pruneIdle(now time.Time) {
	if t.lastPrune.IsZero() {
		t.lastPrune = now
		return
	}
	if now.Sub(t.lastPrune) < idlePruneInterval {
		return
	}
	t.lastPrune = now
	cutoff := now.Add(-t.window)
	for key, samples := range t.seen {
		if len(samples) == 0 || samples[len(samples)-1].Before(cutoff) {
			delete(t.seen, key)
		}
	}
}

// Forget drops a key's history immediately — for an explicit tenant deletion.
// Idle keys are also reclaimed automatically by pruneIdle, so this is an
// optimisation, not the only bound.
func (t *RateTracker) Forget(key string) {
	if t == nil {
		return
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	delete(t.seen, key)
}
