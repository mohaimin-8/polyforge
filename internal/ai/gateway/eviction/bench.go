package eviction

import (
	"fmt"
	"math/rand"
	"sort"
	"time"
)

// Simulator drives one policy over a request stream with an entry-count
// capacity, accounting the dollars a real gateway would have spent. It
// reproduces the semantic cache's defining behavior: a request whose
// prompt sits in the same near-duplicate cluster as any resident entry is
// served from that neighbor (similarity above the admission threshold),
// so keeping two entries of one dense cluster wastes a slot. Classical
// policies cannot see that; the cost-aware density term prices it.
type Simulator struct {
	capacity int
	policy   Policy
	resident map[string]Entry
	clusters map[string]map[string]bool
}

func NewSimulator(policy Policy, capacity int) *Simulator {
	return &Simulator{
		capacity: capacity,
		policy:   policy,
		resident: map[string]Entry{},
		clusters: map[string]map[string]bool{},
	}
}

// Request processes one prompt request; it returns true on cache hit
// (exact or served by a near-duplicate cluster neighbor).
func (s *Simulator) Request(e Entry, now time.Time) bool {
	if _, ok := s.resident[e.ID]; ok {
		s.policy.OnHit(e.ID, now)
		return true
	}
	if e.Cluster != "" {
		if serving, ok := clusterRepresentative(s.clusters[e.Cluster]); ok {
			s.policy.OnHit(serving, now)
			return true
		}
	}
	s.resident[e.ID] = e
	if e.Cluster != "" {
		if s.clusters[e.Cluster] == nil {
			s.clusters[e.Cluster] = map[string]bool{}
		}
		s.clusters[e.Cluster][e.ID] = true
	}
	s.policy.OnStore(e, now)
	for len(s.resident) > s.capacity {
		victim, ok := s.policy.Victim(now)
		if !ok {
			break
		}
		s.remove(victim)
		s.policy.OnRemove(victim)
	}
	return false
}

func (s *Simulator) remove(id string) {
	if e, ok := s.resident[id]; ok {
		delete(s.resident, id)
		if e.Cluster != "" {
			delete(s.clusters[e.Cluster], id)
			if len(s.clusters[e.Cluster]) == 0 {
				delete(s.clusters, e.Cluster)
			}
		}
	}
}

// clusterRepresentative picks the lexicographically first resident of the
// cluster so replays are deterministic regardless of map order.
func clusterRepresentative(members map[string]bool) (string, bool) {
	best := ""
	for id := range members {
		if best == "" || id < best {
			best = id
		}
	}
	return best, best != ""
}

// Result is one policy's benchmark outcome.
type Result struct {
	Policy         string
	Requests       int
	Hits           int
	HitRate        float64
	TotalCostUSD   float64
	CostPerRequest float64
	// OverheadP99 is the 99th-percentile time of one full Request call
	// (lookup + admission + eviction), the ≤5ms gate from the roadmap.
	OverheadP99 time.Duration
}

// tierPrices approximate docs/INFERENCE_BENCH.md $-per-1k-token figures.
var tiers = []struct {
	name        string
	share       float64
	pricePer1kT float64
	meanTokens  float64
}{
	{"local", 0.70, 0.0004, 300},
	{"fast", 0.25, 0.0008, 500},
	{"quality", 0.05, 0.0450, 900},
}

// Workload generates the LMSYS-shaped request stream: a Zipf popularity
// curve over a prompt universe (most prompts unique, a head of repeats —
// the dataset's documented duplication profile), tier assignment by
// share, per-prompt token counts driving recompute cost, and
// near-duplicate clusters of 4 adjacent popularity ranks (LMSYS's
// paraphrase clusters: "write a poem about X" variants). Same seed →
// identical stream.
func Workload(seed int64, requests, universe int) []Entry {
	rng := rand.New(rand.NewSource(seed))
	zipf := rand.NewZipf(rng, 1.07, 1, uint64(universe-1))
	const clusterSpan = 4
	// Topic drift: the hot set rotates through the universe in three
	// phases (LMSYS's diurnal topic churn — yesterday's viral prompt is
	// dead today). Frequency hoarded on a dead phase's head is worthless;
	// policies must notice or pay for it.
	phase := requests/3 + 1

	// Fixed per-prompt attributes so a prompt costs the same every time.
	type prompt struct {
		tier    int
		tokens  float64
		density float64
	}
	prompts := map[uint64]prompt{}
	stream := make([]Entry, requests)
	base := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	for i := range stream {
		id := (zipf.Uint64() + uint64(i/phase)*uint64(universe)/3) % uint64(universe)
		spec, ok := prompts[id]
		if !ok {
			tier := pickTier(rng.Float64())
			spec = prompt{
				tier:   tier,
				tokens: tiers[tier].meanTokens * (0.3 + rng.ExpFloat64()),
				// Density = how popular this prompt's near-duplicate
				// cluster is: head clusters see constant paraphrase
				// traffic, tail prompts are semantically isolated.
				density: 1 / (1 + float64(id/clusterSpan)/25),
			}
			prompts[id] = spec
		}
		cost := spec.tokens / 1000 * tiers[spec.tier].pricePer1kT
		stream[i] = Entry{
			ID:               fmt.Sprintf("p%06d", id),
			SizeBytes:        int64(spec.tokens * 4),
			RecomputeCostUSD: cost,
			EmbeddingDensity: spec.density,
			Cluster:          fmt.Sprintf("c%05d", id/clusterSpan),
			StoredAt:         base,
		}
		// LMSYS-like inter-arrival: ~1.2s mean.
		base = base.Add(time.Duration(rng.ExpFloat64() * 1.2 * float64(time.Second)))
		stream[i].StoredAt = base
	}
	return stream
}

func pickTier(r float64) int {
	acc := 0.0
	for i, t := range tiers {
		acc += t.share
		if r < acc {
			return i
		}
	}
	return len(tiers) - 1
}

// Candidates returns the six policies of the head-to-head table, fresh
// instances sized for the given capacity.
func Candidates(capacity int) []Policy {
	return []Policy{
		NewLRU(),
		NewLFU(),
		NewGDSF(),
		NewARC(capacity),
		NewThresholdLRU(),
		NewCostAware(CostAwareConfig{}),
	}
}

// Run benchmarks one policy over the stream.
func Run(policy Policy, stream []Entry, capacity int) Result {
	sim := NewSimulator(policy, capacity)
	result := Result{Policy: policy.Name(), Requests: len(stream)}
	overheads := make([]time.Duration, len(stream))
	for i, e := range stream {
		started := time.Now()
		hit := sim.Request(e, e.StoredAt)
		overheads[i] = time.Since(started)
		if hit {
			result.Hits++
		} else {
			result.TotalCostUSD += e.RecomputeCostUSD
		}
	}
	result.HitRate = float64(result.Hits) / float64(result.Requests)
	result.CostPerRequest = result.TotalCostUSD / float64(result.Requests)
	result.OverheadP99 = percentileDuration(overheads, 99)
	return result
}

func percentileDuration(values []time.Duration, p int) time.Duration {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]time.Duration(nil), values...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	rank := len(sorted) * p / 100
	if rank >= len(sorted) {
		rank = len(sorted) - 1
	}
	return sorted[rank]
}
