package eviction

import (
	"testing"
	"time"
)

func entry(id string, costUSD, density float64) Entry {
	return Entry{ID: id, SizeBytes: 1000, RecomputeCostUSD: costUSD, EmbeddingDensity: density}
}

func at(offset time.Duration) time.Time {
	return time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC).Add(offset)
}

func TestLRUEvictsLeastRecentlyUsed(t *testing.T) {
	sim := NewSimulator(NewLRU(), 2)
	sim.Request(entry("a", 1, 0), at(0))
	sim.Request(entry("b", 1, 0), at(time.Second))
	sim.Request(entry("a", 1, 0), at(2*time.Second)) // refresh a
	sim.Request(entry("c", 1, 0), at(3*time.Second)) // evicts b
	if !sim.Request(entry("a", 1, 0), at(4*time.Second)) {
		t.Fatal("a should have survived")
	}
	if sim.Request(entry("b", 1, 0), at(5*time.Second)) {
		t.Fatal("b should have been evicted")
	}
}

func TestLFUEvictsLeastFrequent(t *testing.T) {
	sim := NewSimulator(NewLFU(), 2)
	sim.Request(entry("a", 1, 0), at(0))
	sim.Request(entry("a", 1, 0), at(time.Second))
	sim.Request(entry("a", 1, 0), at(2*time.Second))
	sim.Request(entry("b", 1, 0), at(3*time.Second))
	sim.Request(entry("c", 1, 0), at(4*time.Second)) // evicts b (0 hits) not a (2 hits)
	if !sim.Request(entry("a", 1, 0), at(5*time.Second)) {
		t.Fatal("frequent a should have survived")
	}
}

func TestGDSFPrefersEvictingCheapEntries(t *testing.T) {
	sim := NewSimulator(NewGDSF(), 2)
	sim.Request(entry("cheap", 0.001, 0), at(0))
	sim.Request(entry("dear", 0.5, 0), at(time.Second))
	sim.Request(entry("new", 0.01, 0), at(2*time.Second)) // evicts cheap
	if !sim.Request(entry("dear", 0.5, 0), at(3*time.Second)) {
		t.Fatal("expensive entry should have survived GDSF eviction")
	}
	if sim.Request(entry("cheap", 0.001, 0), at(4*time.Second)) {
		t.Fatal("cheap entry should have been the GDSF victim")
	}
}

func TestARCAdaptsAndStaysWithinCapacity(t *testing.T) {
	capacity := 4
	sim := NewSimulator(NewARC(capacity), capacity)
	// Scan pattern then re-references: ARC must not thrash on the scan.
	for i := 0; i < 20; i++ {
		sim.Request(entry(string(rune('a'+i%8)), 1, 0), at(time.Duration(i)*time.Second))
	}
	if len(sim.resident) > capacity {
		t.Fatalf("ARC resident=%d exceeds capacity %d", len(sim.resident), capacity)
	}
	// A hot entry accessed every step must survive.
	sim2 := NewSimulator(NewARC(capacity), capacity)
	for i := 0; i < 30; i++ {
		sim2.Request(entry("hot", 1, 0), at(time.Duration(2*i)*time.Second))
		sim2.Request(entry(string(rune('a'+i%10)), 1, 0), at(time.Duration(2*i+1)*time.Second))
	}
	if !sim2.Request(entry("hot", 1, 0), at(time.Hour)) {
		t.Fatal("hot entry evicted by ARC")
	}
}

func TestCostAwareKeepsExpensiveOverCheapAtEqualRecency(t *testing.T) {
	sim := NewSimulator(NewCostAware(CostAwareConfig{}), 2)
	sim.Request(entry("cheap", 0.0001, 0.2), at(0))
	sim.Request(entry("dear", 0.05, 0.2), at(time.Second))
	sim.Request(entry("new", 0.001, 0.2), at(2*time.Second))
	if !sim.Request(entry("dear", 0.05, 0.2), at(3*time.Second)) {
		t.Fatal("500x more expensive entry must survive")
	}
}

func TestCostAwareStalenessOvercomesCost(t *testing.T) {
	// An expensive entry idle far past the half-life loses to a cheap but
	// hot one: staleness decay must eventually dominate.
	policy := NewCostAware(CostAwareConfig{StalenessHalfLife: time.Minute})
	sim := NewSimulator(policy, 2)
	sim.Request(entry("dear_idle", 0.05, 0.2), at(0))
	sim.Request(entry("cheap_hot", 0.0005, 0.2), at(time.Second))
	for i := 0; i < 10; i++ {
		sim.Request(entry("cheap_hot", 0.0005, 0.2), at(time.Duration(i)*3*time.Minute))
	}
	sim.Request(entry("new", 0.001, 0.2), at(31*time.Minute))
	if !sim.Request(entry("cheap_hot", 0.0005, 0.2), at(32*time.Minute)) {
		t.Fatal("hot entry lost to a long-dead expensive one: staleness term broken")
	}
}

func TestCostAwareEvictsDenseNeighborhoodsFirst(t *testing.T) {
	sim := NewSimulator(NewCostAware(CostAwareConfig{}), 2)
	sim.Request(entry("dense", 0.01, 0.95), at(0))
	sim.Request(entry("isolated", 0.01, 0.02), at(time.Second))
	sim.Request(entry("new", 0.01, 0.5), at(2*time.Second))
	if !sim.Request(entry("isolated", 0.01, 0.02), at(3*time.Second)) {
		t.Fatal("isolated entry must outlive an equally-priced dense one")
	}
}

// The claims this benchmark supports (and the paper repeats): cost-aware
// strictly beats every deployed-practice baseline (LRU, LFU, ARC,
// GPTCache's threshold+LRU) on $-per-request, and stays within 5% of
// GDSF — the strongest classical cost-frequency policy — while adding the
// staleness and semantic-density terms whose payoff needs real substitute
// traffic to measure. Strict dominance over GDSF is deliberately NOT
// asserted: on synthetic streams the two trade the lead by ±1pp with
// scale, and a test pinned to a winning scale would be cherry-picking.
func TestHeadToHeadCostAwareBeatsDeployedBaselines(t *testing.T) {
	stream := Workload(7, 30000, 4000)
	capacity := 400
	results := map[string]Result{}
	for _, policy := range Candidates(capacity) {
		results[policy.Name()] = Run(policy, stream, capacity)
	}
	costAware := results["costaware_w28"]
	for _, name := range []string{"lru", "lfu", "arc", "gptcache_threshold_lru"} {
		if costAware.CostPerRequest >= results[name].CostPerRequest {
			t.Errorf("cost-aware $%.6f/req not below %s $%.6f/req",
				costAware.CostPerRequest, name, results[name].CostPerRequest)
		}
	}
	if limit := results["gdsf"].CostPerRequest * 1.05; costAware.CostPerRequest > limit {
		t.Errorf("cost-aware $%.6f/req more than 5%% behind gdsf $%.6f/req",
			costAware.CostPerRequest, results["gdsf"].CostPerRequest)
	}
	// The roadmap's overhead gate, scaled to the simulator: full request
	// handling stays far inside 5ms even with O(n) victim scans.
	for name, result := range results {
		if result.OverheadP99 > 5*time.Millisecond {
			t.Errorf("%s p99 overhead %s exceeds 5ms", name, result.OverheadP99)
		}
	}
}

func TestWorkloadIsDeterministic(t *testing.T) {
	a := Workload(42, 2000, 500)
	b := Workload(42, 2000, 500)
	for i := range a {
		if a[i] != b[i] {
			t.Fatalf("workload diverged at %d", i)
		}
	}
}
