// Package eviction implements the W28 cost-aware semantic-cache eviction
// algorithm and the five baselines it is benchmarked against (LRU, LFU,
// GDSF, ARC, and GPTCache's default threshold+LRU).
//
// Existing semantic caches evict as if every entry were equally expensive
// to recompute. For AI workloads that is false by orders of magnitude: a
// long quality-tier completion costs ~500x a short local-tier one. The
// cost-aware policy scores every entry
//
//	w = recompute_cost_usd × hit_probability × staleness_decay × 1/(ε+density)
//
// and evicts the minimum-w entry. Formal pseudocode and the analysis live
// in research/paper/sec-eviction.tex.
package eviction

import (
	"container/list"
	"time"
)

// Entry is the metadata a policy may use to value one cache entry.
type Entry struct {
	ID string
	// SizeBytes of the cached completion.
	SizeBytes int64
	// RecomputeCostUSD to regenerate the completion on a miss (token count
	// × the tier price from docs/INFERENCE_BENCH.md).
	RecomputeCostUSD float64
	// EmbeddingDensity in [0,1]: similarity of this entry's prompt to its
	// nearest cached neighbor at store time. Dense regions are
	// substitutable — a near-duplicate can absorb the hit — so density
	// lowers an entry's eviction weight.
	EmbeddingDensity float64
	// Cluster identifies the near-duplicate neighborhood (similarity
	// above the cache admission threshold). Policies never read it; the
	// benchmark simulator uses it to model semantic serving.
	Cluster  string
	StoredAt time.Time
}

// Policy is the pluggable eviction seam. The cache (real or simulated)
// owns membership; the policy owns the ordering. Contract: OnStore for
// every admission, OnHit for every cache hit, OnRemove for any external
// removal, and Victim to choose an eviction — which the cache then
// reports back via OnRemove.
type Policy interface {
	Name() string
	OnStore(e Entry, now time.Time)
	OnHit(id string, now time.Time)
	OnRemove(id string)
	Victim(now time.Time) (string, bool)
}

// ---- LRU ----

type lru struct {
	order *list.List // front = most recent
	items map[string]*list.Element
}

// NewLRU evicts the least-recently-used entry.
func NewLRU() Policy {
	return &lru{order: list.New(), items: map[string]*list.Element{}}
}

func (p *lru) Name() string { return "lru" }

func (p *lru) OnStore(e Entry, _ time.Time) {
	if el, ok := p.items[e.ID]; ok {
		p.order.MoveToFront(el)
		return
	}
	p.items[e.ID] = p.order.PushFront(e.ID)
}

func (p *lru) OnHit(id string, _ time.Time) {
	if el, ok := p.items[id]; ok {
		p.order.MoveToFront(el)
	}
}

func (p *lru) OnRemove(id string) {
	if el, ok := p.items[id]; ok {
		p.order.Remove(el)
		delete(p.items, id)
	}
}

func (p *lru) Victim(_ time.Time) (string, bool) {
	back := p.order.Back()
	if back == nil {
		return "", false
	}
	return back.Value.(string), true
}

// NewThresholdLRU is the GPTCache-default baseline: admission is gated by
// a similarity threshold *upstream* (PolyForge's cache already does this
// for every policy — DefaultCacheThreshold), after which eviction is
// plain LRU. It exists as a named candidate so the benchmark table maps
// 1:1 onto the papers being compared against.
func NewThresholdLRU() Policy {
	return &thresholdLRU{lru{order: list.New(), items: map[string]*list.Element{}}}
}

type thresholdLRU struct{ lru }

func (p *thresholdLRU) Name() string { return "gptcache_threshold_lru" }

// ---- LFU ----

type lfu struct {
	counts map[string]int64
	stored map[string]time.Time
}

// NewLFU evicts the least-frequently-used entry, ties broken by age.
func NewLFU() Policy {
	return &lfu{counts: map[string]int64{}, stored: map[string]time.Time{}}
}

func (p *lfu) Name() string { return "lfu" }

func (p *lfu) OnStore(e Entry, now time.Time) {
	if _, ok := p.counts[e.ID]; !ok {
		p.counts[e.ID] = 0
		p.stored[e.ID] = now
	}
}

func (p *lfu) OnHit(id string, _ time.Time) {
	if _, ok := p.counts[id]; ok {
		p.counts[id]++
	}
}

func (p *lfu) OnRemove(id string) {
	delete(p.counts, id)
	delete(p.stored, id)
}

func (p *lfu) Victim(_ time.Time) (string, bool) {
	var victim string
	var found bool
	for id, count := range p.counts {
		if !found {
			victim, found = id, true
			continue
		}
		if count < p.counts[victim] ||
			(count == p.counts[victim] && p.stored[id].Before(p.stored[victim])) {
			victim = id
		}
	}
	return victim, found
}
