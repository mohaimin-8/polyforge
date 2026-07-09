// Command evictionbench runs the W28 head-to-head: six eviction policies
// over the same seeded LMSYS-shaped workload (Zipf popularity, topic
// drift, near-duplicate clusters, heterogeneous per-tier recompute cost).
//
//	go run ./cmd/evictionbench -out .
//
// writes research/results/eviction_comparison.csv. Same seed → identical
// stream → identical table.
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"polyforge/internal/ai/gateway/eviction"
)

func main() {
	seed := flag.Int64("seed", 7, "workload seed")
	requests := flag.Int("requests", 200000, "requests in the stream")
	universe := flag.Int("universe", 20000, "unique prompts")
	outDir := flag.String("out", ".", "repository root to write results under")
	flag.Parse()

	stream := eviction.Workload(*seed, *requests, *universe)
	// Two regimes: loose (cache holds 10% of the universe) and
	// constrained (2%), where the semantic-density term matters most.
	capacities := []int{*universe / 10, *universe / 50}
	fmt.Printf("workload: %d requests, %d-prompt universe, capacities %v, seed %d\n",
		*requests, *universe, capacities, *seed)

	csv := "policy,requests,capacity_entries,hit_rate,total_cost_usd,cost_per_request_usd,savings_vs_lru,overhead_p99_us\n"
	for _, capacity := range capacities {
		var results []eviction.Result
		for _, policy := range eviction.Candidates(capacity) {
			results = append(results, eviction.Run(policy, stream, capacity))
		}
		var lruCost float64
		for _, r := range results {
			if r.Policy == "lru" {
				lruCost = r.CostPerRequest
			}
		}
		fmt.Printf("\ncapacity %d entries:\n%-24s %9s %14s %12s %14s %12s\n",
			capacity, "policy", "hit_rate", "total_cost_$", "$/request", "vs_lru", "p99_overhead")
		for _, r := range results {
			savings := 0.0
			if lruCost > 0 {
				savings = 1 - r.CostPerRequest/lruCost
			}
			csv += fmt.Sprintf("%s,%d,%d,%.4f,%.4f,%.8f,%.4f,%.1f\n",
				r.Policy, r.Requests, capacity, r.HitRate, r.TotalCostUSD,
				r.CostPerRequest, savings, float64(r.OverheadP99.Nanoseconds())/1000)
			fmt.Printf("%-24s %8.2f%% %14.2f %12.8f %+11.1f%% %12s\n",
				r.Policy, r.HitRate*100, r.TotalCostUSD, r.CostPerRequest,
				savings*100, r.OverheadP99.Round(time.Microsecond))
		}
	}

	path := filepath.Join(*outDir, "research", "results", "eviction_comparison.csv")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := os.WriteFile(path, []byte(csv), 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("\nwrote %s\n", path)
}
