//go:build race

package eviction

// raceEnabled reports whether the race detector instruments this test
// binary; it slows code several-fold, so wall-clock gates widen under it.
const raceEnabled = true
