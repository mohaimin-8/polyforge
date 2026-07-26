package controllers

import (
	"crypto/md5" // #nosec G501 -- partition key, not a security primitive
	"encoding/hex"
	"sort"

	"polyforge/internal/operator/planner"
)

// Planning cells: the shipped form of the Wave 4 scaling result.
//
// The joint planner's cost is super-linear in tenants (measured exponent 1.59
// monolithic), so its p95 first crosses the operator's 3 s timeout at 128
// tenants (`research/analysis/PLANNER_CELLS.md`, PS-H1). Partitioning the
// portfolio into fixed-size cells and planning each separately keeps per-cell
// latency flat — measured exponent 0.27, ~195 ms per cell at every N through
// 1024 — which is what lets 1024 tenants plan inside the deadline.
//
// Until now that result lived only in the research simulator: the operator
// always issued one call for every tenant, so the deployed system would have
// hit the same 128-tenant wall the paper says it clears. This closes that gap.
//
// The partition rule is the *de-aliased* one, and the distinction is not
// cosmetic. The originally frozen rule (round-robin on tenant index) aliases
// with any periodic structure in the portfolio: with a whale every 8th tenant,
// every cell count that is a multiple of 8 collapsed the whales into a few
// cells and global Jain fell by up to 0.089. Ordering by md5(tenant_id) and
// chunking decorrelates membership from index and cannot alias, holding the
// worst-case global-Jain change to −0.0053 across N ∈ [8, 1024]
// (`PLANNER_CELLS_DEALIAS.md`, PF-H1/PF-H2).
//
// What partitioning costs, stated plainly: fairness becomes an objective
// *within* each cell rather than across the portfolio. The measured price of
// that is the −0.0053 above, small but not zero, and it is why cells are
// opt-in rather than the default.

// CellSize is the fixed number of tenants per planning cell — `CELL_SIZE` in
// `research/analysis/planner_cells.py`, the size every published per-cell
// latency was measured at. Cells are sized, not counted: the cell count grows
// with the portfolio so per-cell solve time stays constant.
const CellSize = 32

// planningCells partitions tenants for one planning cycle.
//
// A cellSize of 0 or less, or a portfolio that fits in a single cell, yields
// exactly one cell holding every tenant in the caller's original order — which
// is byte-for-byte the single-call behaviour the operator had before cells
// existed. That is what makes the feature safe to ship on by default in the
// only sense that matters: switched off, nothing moves.
//
// Otherwise tenants are ordered by md5(tenant_id) and chunked into
// consecutive groups of cellSize, mirroring `planner_cells_dealias.hash_cells`.
// Ties in the digest (only possible on duplicate tenant IDs, which the API
// forbids) fall back to the ID itself so the partition is total and stable.
func planningCells(inputs []planner.TenantInput, cellSize int) [][]planner.TenantInput {
	if cellSize <= 0 || len(inputs) <= cellSize {
		return [][]planner.TenantInput{inputs}
	}
	ordered := make([]planner.TenantInput, len(inputs))
	copy(ordered, inputs)
	digest := make(map[string]string, len(ordered))
	for _, in := range ordered {
		sum := md5.Sum([]byte(in.TenantID)) // #nosec G401 -- partition key only
		digest[in.TenantID] = hex.EncodeToString(sum[:])
	}
	sort.SliceStable(ordered, func(i, j int) bool {
		a, b := digest[ordered[i].TenantID], digest[ordered[j].TenantID]
		if a == b {
			return ordered[i].TenantID < ordered[j].TenantID
		}
		return a < b
	})
	cells := make([][]planner.TenantInput, 0, (len(ordered)+cellSize-1)/cellSize)
	for i := 0; i < len(ordered); i += cellSize {
		end := i + cellSize
		if end > len(ordered) {
			end = len(ordered)
		}
		cells = append(cells, ordered[i:end])
	}
	return cells
}

// cellLimits apportions the cluster-wide ceilings across a cycle's cells,
// returning one Limits per cell in the same order.
//
// The measured protocol sizes a payload's limits by how many tenants it
// carries (`cell_payload`: 3·k replicas, 256·k MB for a k-tenant cell, against
// 3·n and 256·n for the monolithic call). The operator's Limits are absolute
// cluster ceilings rather than per-tenant rates, so the equivalent is a
// proportional split: a cell holding k of n tenants may claim k/n of the
// cluster.
//
// The split is computed for all cells together because the rounding has to be
// reconciled globally: the shares must sum to exactly the cluster ceiling —
// never above, which would let separately planned cells jointly overcommit the
// cluster, and never silently below, which would strand capacity the portfolio
// is entitled to.
func cellLimits(total planner.Limits, cells [][]planner.TenantInput) []planner.Limits {
	sizes := make([]int, len(cells))
	for i, cell := range cells {
		sizes[i] = len(cell)
	}
	replicas := apportion(total.Replicas, sizes)
	cacheMB := apportion(total.CacheMB, sizes)
	out := make([]planner.Limits, len(cells))
	for i := range cells {
		out[i] = planner.Limits{Replicas: replicas[i], CacheMB: cacheMB[i]}
	}
	return out
}

// apportion splits total across cells in proportion to sizes, by largest
// remainder: each cell takes floor(total·kᵢ/n), then the leftover units — of
// which there are strictly fewer than the number of cells — go one each to the
// cells with the largest fractional parts, ties broken by index so the result
// is deterministic. The returned shares sum to exactly total.
func apportion(total int32, sizes []int) []int32 {
	out := make([]int32, len(sizes))
	if total <= 0 || len(sizes) == 0 {
		return out
	}
	var n int64
	for _, k := range sizes {
		n += int64(k)
	}
	if n <= 0 {
		return out
	}
	type frac struct {
		index     int
		remainder int64
	}
	fracs := make([]frac, 0, len(sizes))
	assigned := int64(0)
	for i, k := range sizes {
		product := int64(total) * int64(k)
		out[i] = int32(product / n)
		assigned += product / n
		fracs = append(fracs, frac{index: i, remainder: product % n})
	}
	sort.SliceStable(fracs, func(a, b int) bool {
		if fracs[a].remainder == fracs[b].remainder {
			return fracs[a].index < fracs[b].index
		}
		return fracs[a].remainder > fracs[b].remainder
	})
	for i := int64(0); i < int64(total)-assigned && i < int64(len(fracs)); i++ {
		out[fracs[i].index]++
	}
	return out
}
