package controllers

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"testing"
	"time"

	"polyforge/internal/operator/planner"
)

// Does the shipped fan-out actually clear the wall the paper says it clears?
//
// PLANNER_CELLS.md measured that in the *simulator*, and until this session
// the operator had no cell logic at all — so the deployed system would still
// have hit the 128-tenant timeout the paper reports clearing. This drives the
// operator's own partition and limit-splitting through the real Python planner
// over HTTP and records what the shipped path does.
//
// Infra-gated, per the repo convention (see planner/live_test.go):
//
//	python services/planner/planner.py --port 8090 &
//	POLYFORGE_TEST_PLANNER_URL=http://127.0.0.1:8090 \
//	  go test ./internal/operator/controllers/ -run CellsScale -v -timeout 30m
//
// Set POLYFORGE_CELLS_SCALE_OUT to write the measurements as JSON for
// research/analysis/cells_shipped.py to turn into the engineering note.
//
// This is engineering verification, not a scored campaign: it re-tests a
// closed result's *implementation*, changes no published number, and needs no
// pre-registration.

// scaleTenant mirrors research/analysis/planner_cells.py::tenant_entry —
// heterogeneous portfolio, every 8th tenant a whale at 4x demand — so the
// shipped path faces the same portfolio shape the published latencies were
// measured against. The jitter there is seeded Gaussian; here it is a fixed
// deterministic ripple, because this test measures solve *time*, which does
// not depend on the noise draw, and a reproducible portfolio is worth more.
func scaleTenant(i int) planner.TenantInput {
	scale := 1.0
	if i%8 == 0 {
		scale = 4.0
	}
	jitter := 1.0 + 0.2*float64((i%5)-2)/2.0
	return planner.TenantInput{
		TenantID:        fmt.Sprintf("t%04d", i),
		SLOClass:        "standard",
		HourlyBudgetUSD: map[bool]float64{true: 12.0, false: 5.0}[i%8 == 0],
		ReplicaMin:      1,
		ReplicaMax:      6,
		State:           planner.State{Replicas: 2, CacheMB: 128, Tier: "small"},
		Demand: planner.Demand{
			RPS: map[string]float64{
				"chat":      max(0.1, 4.0*scale*jitter),
				"embed":     max(0.1, 3.0*scale*jitter),
				"crud_read": max(0.1, 5.0*scale*jitter),
			},
			CrudBaseMs: 60.0,
		},
	}
}

type cellScaleRow struct {
	Tenants     int     `json:"tenants"`
	CellSize    int     `json:"cell_size"`
	Cells       int     `json:"cells"`
	PerCellP95  float64 `json:"per_cell_p95_ms"`
	PerCellMed  float64 `json:"per_cell_median_ms"`
	CycleTotal  float64 `json:"cycle_total_ms"`
	PlansOut    int     `json:"plans_returned"`
	WithinCycle bool    `json:"within_10s_control_period"`
}

func TestCellsScaleOnShippedPath(t *testing.T) {
	url := os.Getenv("POLYFORGE_TEST_PLANNER_URL")
	if url == "" {
		if os.Getenv("POLYFORGE_REQUIRE_PLANNER") != "" {
			t.Fatal("POLYFORGE_REQUIRE_PLANNER is set but " +
				"POLYFORGE_TEST_PLANNER_URL is not: the shipped cell-scaling " +
				"path would go unexercised and the run would still report ok")
		}
		t.Skip("POLYFORGE_TEST_PLANNER_URL not set; shipped-path cell scaling not exercised")
	}
	client := planner.NewHTTPClient(url, 120*time.Second).
		WithAuthToken(os.Getenv("POLYFORGE_PLANNER_TOKEN"))

	var rows []cellScaleRow
	for _, n := range []int{8, 32, 64, 128, 256} {
		for _, cellSize := range []int{0, CellSize} {
			inputs := make([]planner.TenantInput, n)
			for i := range inputs {
				inputs[i] = scaleTenant(i)
			}
			cells := planningCells(inputs, cellSize)
			limits := cellLimits(planner.Limits{Replicas: int32(3 * n), CacheMB: int32(256 * n)}, cells)

			plans := map[string]planner.Plan{}
			var durations []float64
			cycleStart := time.Now()
			for i, cell := range cells {
				start := time.Now()
				ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
				resp, err := client.Plan(ctx, planner.Request{
					Weights: planner.Weights{Alpha: 1, Beta: 2, Gamma: 0.5},
					Limits:  limits[i],
					Tenants: cell,
				})
				cancel()
				if err != nil {
					t.Fatalf("n=%d cellSize=%d cell %d: %v", n, cellSize, i, err)
				}
				durations = append(durations, float64(time.Since(start).Microseconds())/1000.0)
				for id, p := range resp.Plans {
					plans[id] = p
				}
			}
			cycle := float64(time.Since(cycleStart).Microseconds()) / 1000.0

			if len(plans) != n {
				t.Errorf("n=%d cellSize=%d: %d plans for %d tenants", n, cellSize, len(plans), n)
			}
			sort.Float64s(durations)
			row := cellScaleRow{
				Tenants: n, CellSize: cellSize, Cells: len(cells),
				PerCellP95: percentile(durations, 0.95),
				PerCellMed: percentile(durations, 0.50),
				CycleTotal: cycle, PlansOut: len(plans),
				WithinCycle: cycle < 10_000,
			}
			rows = append(rows, row)
			t.Logf("n=%4d cellSize=%2d cells=%3d per-cell p95=%8.1fms median=%8.1fms cycle=%9.1fms",
				n, cellSize, len(cells), row.PerCellP95, row.PerCellMed, cycle)
		}
	}

	if out := os.Getenv("POLYFORGE_CELLS_SCALE_OUT"); out != "" {
		blob, err := json.MarshalIndent(rows, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(out, blob, 0o600); err != nil {
			t.Fatal(err)
		}
		t.Logf("wrote %s", out)
	}
}

func percentile(sorted []float64, q float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	idx := int(q * float64(len(sorted)-1))
	return sorted[idx]
}
