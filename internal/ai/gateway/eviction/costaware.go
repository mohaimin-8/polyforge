package eviction

import (
	"math"
	"time"
)

// CostAwareConfig tunes the W28 algorithm. Zero values take the defaults
// used in the paper's benchmark.
type CostAwareConfig struct {
	// StalenessHalfLife is the exponential-decay half-life applied to time
	// since last access. Default 6h: an entry idle for 6h is worth half an
	// active one, all else equal.
	StalenessHalfLife time.Duration
	// HitRateHalfLife smooths the observed hit rate over time. Default 1h.
	HitRateHalfLife time.Duration
	// DensityEpsilon bounds the isolation bonus: an entry with no semantic
	// neighbors is worth at most (1+ε)/ε times a fully dense one. Default
	// 0.5, i.e. a ≤3x tiebreaker — substitutability may break ties between
	// comparable entries but never outvote measured hit economics.
	DensityEpsilon float64
}

func (cfg CostAwareConfig) withDefaults() CostAwareConfig {
	if cfg.StalenessHalfLife <= 0 {
		cfg.StalenessHalfLife = 6 * time.Hour
	}
	if cfg.HitRateHalfLife <= 0 {
		cfg.HitRateHalfLife = time.Hour
	}
	if cfg.DensityEpsilon <= 0 {
		cfg.DensityEpsilon = 0.5
	}
	return cfg
}

type costAwareEntry struct {
	cost       float64
	density    float64
	storedAt   time.Time
	lastAccess time.Time
	hits       float64
	// inflation is the GreedyDual L value captured at admission or last
	// hit (Cao & Irani 1997): every eviction raises the floor, so an
	// entry must keep re-earning its place against ever-newer arrivals.
	inflation float64
}

// costAware implements the paper's eviction weight:
//
//	w(e) = recompute_cost(e) × hit_probability(e) × staleness_decay(e) × 1/(ε + density(e))
//
// evict argmin w. Each factor prices one failure mode of the baselines:
// cost (LRU/LFU/ARC are cost-blind), hit probability (GDSF's raw frequency
// never ages per entry), staleness (LFU keeps dead-but-once-hot entries
// forever), and density (no classical policy knows that a near-duplicate
// neighbor can absorb a hit after eviction — the semantic-cache-specific
// term).
type costAware struct {
	cfg     CostAwareConfig
	entries map[string]*costAwareEntry
	l       float64
}

// NewCostAware builds the W28 policy.
func NewCostAware(cfg CostAwareConfig) Policy {
	return &costAware{cfg: cfg.withDefaults(), entries: map[string]*costAwareEntry{}}
}

func (p *costAware) Name() string { return "costaware_w28" }

func (p *costAware) OnStore(e Entry, now time.Time) {
	p.entries[e.ID] = &costAwareEntry{
		cost:       e.RecomputeCostUSD,
		density:    e.EmbeddingDensity,
		storedAt:   now,
		lastAccess: now,
		inflation:  p.l,
	}
}

func (p *costAware) OnHit(id string, now time.Time) {
	if entry, ok := p.entries[id]; ok {
		entry.hits++
		entry.lastAccess = now
		entry.inflation = p.l
	}
}

func (p *costAware) OnRemove(id string) {
	delete(p.entries, id)
}

func (p *costAware) Victim(now time.Time) (string, bool) {
	var victim string
	var worst float64
	found := false
	for id, entry := range p.entries {
		w := p.weight(entry, now)
		if !found || w < worst {
			victim, worst, found = id, w, true
		}
	}
	if found {
		p.l = worst
	}
	return victim, found
}

// weight computes w(e) at time now.
func (p *costAware) weight(e *costAwareEntry, now time.Time) float64 {
	// hit_probability estimator: Laplace-smoothed hits per
	// HitRateHalfLife of lifetime. The +1 prior means a fresh expensive
	// entry is not evicted before it can prove itself; dividing by
	// lifetime periods means an entry must keep earning its rate — unlike
	// LFU/GDSF frequency, which once accumulated never decays.
	lifetime := now.Sub(e.storedAt)
	periods := 1 + lifetime.Seconds()/p.cfg.HitRateHalfLife.Seconds()
	hitRate := (e.hits + 1) / periods

	idle := now.Sub(e.lastAccess)
	staleness := math.Exp2(-idle.Seconds() / p.cfg.StalenessHalfLife.Seconds())

	isolation := 1 / (p.cfg.DensityEpsilon + e.density)

	return e.inflation + e.cost*hitRate*staleness*isolation
}
