package planner

import (
	"context"
	"os"
	"testing"
	"time"
)

// TestLivePlannerContract exercises the real Python planner service across
// the language boundary. It follows the repo convention for infra-gated
// tests: skipped unless the environment provides the dependency.
//
//	python services/planner/planner.py --port 8090 &
//	POLYFORGE_TEST_PLANNER_URL=http://127.0.0.1:8090 go test ./internal/operator/planner/ -run Live -v
func TestLivePlannerContract(t *testing.T) {
	url := os.Getenv("POLYFORGE_TEST_PLANNER_URL")
	if url == "" {
		t.Skip("POLYFORGE_TEST_PLANNER_URL not set; live planner contract not exercised")
	}

	client := NewHTTPClient(url, 5*time.Second)
	req := planRequest()
	// Drive a sustained surge so the planner's forecast sees growth; the
	// W31 verification is scale-up within two control cycles.
	req.Tenants[0].Demand = Demand{RPS: map[string]float64{"crud_read": 400.0}, CrudBaseMs: 50}

	var last Response
	for cycle := range 3 {
		resp, err := client.Plan(context.Background(), req)
		if err != nil {
			t.Fatalf("cycle %d: %v", cycle, err)
		}
		last = resp
		plan := resp.Plans["acme"]
		req.Tenants[0].State = State{Replicas: plan.Replicas, CacheMB: plan.CacheMB, Tier: plan.Tier}
	}
	if last.Solver == "" {
		t.Error("solver name missing")
	}
	if got := last.Plans["acme"].Replicas; got <= 2 {
		t.Errorf("after 3 surge cycles replicas = %d, want > 2", got)
	}
}
