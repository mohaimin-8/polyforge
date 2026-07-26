package controllers

import (
	"crypto/md5" // #nosec G501 -- mirrors the partition key under test
	"encoding/hex"
	"fmt"
	"sort"
	"testing"

	"polyforge/internal/operator/planner"
)

func tenants(n int) []planner.TenantInput {
	out := make([]planner.TenantInput, n)
	for i := range out {
		out[i] = planner.TenantInput{TenantID: fmt.Sprintf("t%04d", i)}
	}
	return out
}

func ids(cell []planner.TenantInput) []string {
	out := make([]string, len(cell))
	for i, in := range cell {
		out[i] = in.TenantID
	}
	return out
}

// The feature has to be invisible until switched on: a disabled or
// larger-than-portfolio cell size must produce the single call the operator
// made before cells existed, with the tenant order untouched.
func TestPlanningCellsDisabledIsOneUntouchedCell(t *testing.T) {
	in := tenants(100)
	for _, size := range []int{0, -1, 100, 1000} {
		cells := planningCells(in, size)
		if len(cells) != 1 {
			t.Fatalf("cellSize=%d: got %d cells, want 1", size, len(cells))
		}
		got := ids(cells[0])
		for i, id := range got {
			if id != in[i].TenantID {
				t.Fatalf("cellSize=%d: order changed at %d: %s != %s",
					size, i, id, in[i].TenantID)
			}
		}
	}
}

// Every tenant is planned exactly once. A tenant silently dropped by the
// partition would keep its previous Policy forever with nothing reporting it.
func TestPlanningCellsPartitionIsExact(t *testing.T) {
	for _, n := range []int{33, 64, 128, 256, 1024} {
		cells := planningCells(tenants(n), CellSize)
		seen := map[string]int{}
		for _, cell := range cells {
			if len(cell) > CellSize {
				t.Fatalf("n=%d: cell of %d exceeds CellSize %d", n, len(cell), CellSize)
			}
			for _, in := range cell {
				seen[in.TenantID]++
			}
		}
		if len(seen) != n {
			t.Fatalf("n=%d: %d distinct tenants planned, want %d", n, len(seen), n)
		}
		for id, count := range seen {
			if count != 1 {
				t.Fatalf("n=%d: tenant %s planned %d times", n, id, count)
			}
		}
		wantCells := (n + CellSize - 1) / CellSize
		if len(cells) != wantCells {
			t.Fatalf("n=%d: %d cells, want %d", n, len(cells), wantCells)
		}
	}
}

// The partition must be md5-ordered chunking, not modulo bucketing. Those two
// rules produce different memberships, and only the former is what
// PLANNER_CELLS_DEALIAS.md measured -- shipping the other would put the
// artifact back out of step with the paper.
func TestPlanningCellsMatchesMeasuredHashOrderRule(t *testing.T) {
	in := tenants(96)
	cells := planningCells(in, CellSize)

	expected := make([]string, len(in))
	for i, tin := range in {
		expected[i] = tin.TenantID
	}
	sort.SliceStable(expected, func(a, b int) bool {
		ha := md5.Sum([]byte(expected[a])) // #nosec G401
		hb := md5.Sum([]byte(expected[b])) // #nosec G401
		return hex.EncodeToString(ha[:]) < hex.EncodeToString(hb[:])
	})

	var got []string
	for _, cell := range cells {
		got = append(got, ids(cell)...)
	}
	for i := range expected {
		if got[i] != expected[i] {
			t.Fatalf("position %d: got %s, want %s (partition is not md5-order-then-chunk)",
				i, got[i], expected[i])
		}
	}
}

// Membership must not track tenant index -- that is the whole point of the
// de-aliased rule. With a whale every 8th tenant, index-based assignment
// concentrated whales and cost up to 0.089 of global Jain.
func TestPlanningCellsDecorrelateFromTenantIndex(t *testing.T) {
	cells := planningCells(tenants(256), CellSize)
	whalesPerCell := make([]int, len(cells))
	for c, cell := range cells {
		for _, in := range cell {
			var idx int
			if _, err := fmt.Sscanf(in.TenantID, "t%04d", &idx); err != nil {
				t.Fatalf("unexpected id %s", in.TenantID)
			}
			if idx%8 == 0 {
				whalesPerCell[c]++
			}
		}
	}
	// 32 whales over 8 cells: 4 each under a perfect spread. Modulo-on-index
	// would put all 32 in one cell. Assert no cell hoards them.
	for c, count := range whalesPerCell {
		if count > 12 {
			t.Errorf("cell %d holds %d of 32 whales -- aliasing with tenant index: %v",
				c, count, whalesPerCell)
		}
	}
}

func TestPlanningCellsIsDeterministic(t *testing.T) {
	a := planningCells(tenants(200), CellSize)
	b := planningCells(tenants(200), CellSize)
	for i := range a {
		x, y := ids(a[i]), ids(b[i])
		for j := range x {
			if x[j] != y[j] {
				t.Fatalf("cell %d position %d differs between runs", i, j)
			}
		}
	}
}

// Concurrently planned cells must not be able to jointly overcommit the
// cluster, and must not strand capacity either: the shares sum to exactly the
// ceiling.
func TestCellLimitsSumToTheClusterCeiling(t *testing.T) {
	for _, n := range []int{33, 64, 100, 128, 257, 1024} {
		cells := planningCells(tenants(n), CellSize)
		total := planner.Limits{Replicas: 60, CacheMB: 4096}
		limits := cellLimits(total, cells)
		var replicas, cache int32
		for _, l := range limits {
			replicas += l.Replicas
			cache += l.CacheMB
		}
		if replicas != total.Replicas {
			t.Errorf("n=%d: replica shares sum to %d, want %d", n, replicas, total.Replicas)
		}
		if cache != total.CacheMB {
			t.Errorf("n=%d: cache shares sum to %d, want %d", n, cache, total.CacheMB)
		}
	}
}

// A single cell must receive the whole ceiling untouched -- the disabled path
// has to be identical to the pre-cells request, limits included.
func TestCellLimitsSingleCellIsUnchanged(t *testing.T) {
	cells := planningCells(tenants(10), 0)
	total := planner.Limits{Replicas: 60, CacheMB: 4096}
	limits := cellLimits(total, cells)
	if len(limits) != 1 || limits[0] != total {
		t.Fatalf("single cell got %+v, want %+v", limits, total)
	}
}

// Shares track cell population: with equal cells they are equal, and a short
// final cell gets proportionally less rather than an equal split.
func TestCellLimitsAreProportionalToPopulation(t *testing.T) {
	cells := planningCells(tenants(80), CellSize) // 32 + 32 + 16
	limits := cellLimits(planner.Limits{Replicas: 100, CacheMB: 800}, cells)
	if len(limits) != 3 {
		t.Fatalf("got %d cells, want 3", len(limits))
	}
	sizes := []int{len(cells[0]), len(cells[1]), len(cells[2])}
	if sizes[0] != 32 || sizes[1] != 32 || sizes[2] != 16 {
		t.Fatalf("unexpected cell sizes %v", sizes)
	}
	if limits[0].Replicas != limits[1].Replicas {
		t.Errorf("equal cells got unequal replica shares: %d vs %d",
			limits[0].Replicas, limits[1].Replicas)
	}
	if limits[2].Replicas >= limits[0].Replicas {
		t.Errorf("the 16-tenant cell got %d replicas, not less than the 32-tenant cell's %d",
			limits[2].Replicas, limits[0].Replicas)
	}
	// 16/80 of 800 MB is exactly 160.
	if limits[2].CacheMB != 160 {
		t.Errorf("short cell cache share = %d, want 160", limits[2].CacheMB)
	}
}

func TestCellLimitsZeroCeilingStaysZero(t *testing.T) {
	cells := planningCells(tenants(64), CellSize)
	for _, l := range cellLimits(planner.Limits{}, cells) {
		if l.Replicas != 0 || l.CacheMB != 0 {
			t.Fatalf("zero ceiling produced %+v", l)
		}
	}
}

// A ceiling smaller than the cell count cannot give every cell a unit; the
// units that exist must still be handed out, not lost.
func TestCellLimitsScarceCeilingIsFullyDistributed(t *testing.T) {
	cells := planningCells(tenants(128), CellSize) // 4 cells
	limits := cellLimits(planner.Limits{Replicas: 3, CacheMB: 1}, cells)
	var replicas, cache int32
	for _, l := range limits {
		replicas += l.Replicas
		cache += l.CacheMB
	}
	if replicas != 3 || cache != 1 {
		t.Fatalf("scarce ceiling lost units: replicas=%d cache=%d", replicas, cache)
	}
}
