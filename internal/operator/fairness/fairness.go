// Package fairness holds the W32 multi-tenant fairness pieces: Jain's
// index over per-tenant SLO satisfaction, and a noisy-neighbor detector
// that turns eBPF-shaped interference signals into the per-tenant score
// the JCAC planner penalizes expansion with.
//
// The detector consumes samples through Observe; the production feed is a
// Pixie/Hubble adapter (per-pod CPU stalls, syscall storms, memory
// pressure), which requires a live cluster and is wired in the W33
// harness environment. Everything downstream of the sample — scoring,
// flagging, planner throttling — is implemented and tested here.
package fairness

import (
	"context"
	"sort"
	"sync"
)

// JainIndex computes Jain's fairness index over non-negative values:
// 1.0 when all tenants are equally satisfied, approaching 1/n as one
// tenant monopolizes. Empty or all-zero input is vacuously fair.
func JainIndex(values []float64) float64 {
	if len(values) == 0 {
		return 1.0
	}
	var sum, squares float64
	for _, v := range values {
		sum += v
		squares += v * v
	}
	if squares == 0 {
		return 1.0
	}
	return sum * sum / (float64(len(values)) * squares)
}

// Sample is one tenant's interference signals over one observation window.
// The fields mirror what eBPF exposes per pod, aggregated per tenant.
type Sample struct {
	TenantID string
	// CPUStallShare is the fraction of the window the tenant's pods spent
	// stalled on CPU (PSI cpu some/full), 0..1.
	CPUStallShare float64
	// SyscallRate is syscalls per second across the tenant's pods.
	SyscallRate float64
	// MemPressureEvents counts memory PSI threshold crossings.
	MemPressureEvents float64
}

// DetectorConfig tunes the flagging behavior.
type DetectorConfig struct {
	// FlagThreshold is the score above which a window counts as noisy.
	FlagThreshold float64
	// FlagWindows is how many consecutive noisy windows flag the tenant
	// (3 windows at the 10 s control interval = flagged within 30 s).
	FlagWindows int
	// ClearWindows is how many consecutive clean windows unflag it.
	ClearWindows int
}

func (c DetectorConfig) withDefaults() DetectorConfig {
	if c.FlagThreshold <= 0 {
		c.FlagThreshold = 2.0
	}
	if c.FlagWindows <= 0 {
		c.FlagWindows = 3
	}
	if c.ClearWindows <= 0 {
		c.ClearWindows = 6
	}
	return c
}

type tenantTrack struct {
	score       float64
	noisyStreak int
	cleanStreak int
	flagged     bool
}

// Detector flags tenants whose interference signals are outliers against
// their peers. Scores are ratios to the cluster median, so a uniformly
// busy cluster flags nobody — noisy means *louder than the neighbors*,
// not merely loud.
type Detector struct {
	mu     sync.RWMutex
	config DetectorConfig
	tracks map[string]*tenantTrack
}

func NewDetector(config DetectorConfig) *Detector {
	return &Detector{
		config: config.withDefaults(),
		tracks: make(map[string]*tenantTrack),
	}
}

// Observe ingests one window of samples covering every active tenant and
// updates flags. Call it once per control interval.
func (d *Detector) Observe(samples []Sample) {
	if len(samples) == 0 {
		return
	}
	stallMed := median(samples, func(s Sample) float64 { return s.CPUStallShare })
	sysMed := median(samples, func(s Sample) float64 { return s.SyscallRate })
	memMed := median(samples, func(s Sample) float64 { return s.MemPressureEvents })

	d.mu.Lock()
	defer d.mu.Unlock()
	seen := make(map[string]bool, len(samples))
	for _, s := range samples {
		seen[s.TenantID] = true
		// Each signal contributes its excess over the cluster median;
		// epsilon floors keep an idle cluster from dividing by zero.
		score := ratioExcess(s.CPUStallShare, stallMed, 0.01) +
			ratioExcess(s.SyscallRate, sysMed, 100) +
			ratioExcess(s.MemPressureEvents, memMed, 1)

		track := d.tracks[s.TenantID]
		if track == nil {
			track = &tenantTrack{}
			d.tracks[s.TenantID] = track
		}
		track.score = score
		if score >= d.config.FlagThreshold {
			track.noisyStreak++
			track.cleanStreak = 0
			if track.noisyStreak >= d.config.FlagWindows {
				track.flagged = true
			}
		} else {
			track.cleanStreak++
			track.noisyStreak = 0
			if track.cleanStreak >= d.config.ClearWindows {
				track.flagged = false
			}
		}
	}
	// Tenants that vanished from the feed no longer interfere.
	for tid := range d.tracks {
		if !seen[tid] {
			delete(d.tracks, tid)
		}
	}
}

// TenantInterference satisfies the PlanRunner's InterferenceSource: zero
// until a tenant is flagged, then its current score. The flag streak is
// the hysteresis — one loud window does not throttle anyone.
func (d *Detector) TenantInterference(_ context.Context, tenantID string) float64 {
	d.mu.RLock()
	defer d.mu.RUnlock()
	track := d.tracks[tenantID]
	if track == nil || !track.flagged {
		return 0
	}
	return track.score
}

// Flagged lists currently flagged tenants, for dashboards and tests.
func (d *Detector) Flagged() []string {
	d.mu.RLock()
	defer d.mu.RUnlock()
	var out []string
	for tid, track := range d.tracks {
		if track.flagged {
			out = append(out, tid)
		}
	}
	sort.Strings(out)
	return out
}

// ratioExcess is how many multiples of the peer median a value sits above
// it: 0 at or below the median, 1.0 at 2× the median, and so on.
func ratioExcess(value, med, epsilon float64) float64 {
	if med < epsilon {
		med = epsilon
	}
	excess := value/med - 1.0
	if excess < 0 {
		return 0
	}
	return excess
}

func median(samples []Sample, pick func(Sample) float64) float64 {
	values := make([]float64, len(samples))
	for i, s := range samples {
		values[i] = pick(s)
	}
	sort.Float64s(values)
	n := len(values)
	if n%2 == 1 {
		return values[n/2]
	}
	return (values[n/2-1] + values[n/2]) / 2
}
