package classifier

import (
	"math"
	"math/rand"
	"path/filepath"
	"testing"
)

func driftFixture(t *testing.T) (*DriftDetector, []Example) {
	t.Helper()
	examples := SyntheticExamples(rand.New(rand.NewSource(11)), 100)
	detector, err := NewDriftDetector(examples)
	if err != nil {
		t.Fatal(err)
	}
	return detector, examples
}

func TestUndriftedPopulationStaysBelowWatchLevel(t *testing.T) {
	detector, _ := driftFixture(t)
	fresh := SyntheticExamples(rand.New(rand.NewSource(12)), 100)
	vectors := make([][FeatureCount]float64, len(fresh))
	for i, ex := range fresh {
		vectors[i] = ex.Features
	}
	drifted, feature, psi := detector.Drifted(vectors)
	if drifted {
		t.Fatalf("same-distribution population flagged: %s psi=%.3f", feature, psi)
	}
	if psi > 0.1 {
		t.Fatalf("same-distribution PSI %.3f above the 0.1 watch bar (%s)", psi, feature)
	}
}

func TestThreeSigmaShiftFiresDriftAlert(t *testing.T) {
	detector, examples := driftFixture(t)
	// Estimate σ of avg_latency_ms (index 4) from the reference set, then
	// shift the live population's latency by 3σ — the W27 verification
	// scenario.
	var mean, variance float64
	for _, ex := range examples {
		mean += ex.Features[4]
	}
	mean /= float64(len(examples))
	for _, ex := range examples {
		variance += (ex.Features[4] - mean) * (ex.Features[4] - mean)
	}
	sigma := math.Sqrt(variance / float64(len(examples)))

	fresh := SyntheticExamples(rand.New(rand.NewSource(13)), 100)
	vectors := make([][FeatureCount]float64, len(fresh))
	for i, ex := range fresh {
		vectors[i] = ex.Features
		vectors[i][4] += 3 * sigma
	}
	drifted, feature, psi := detector.Drifted(vectors)
	if !drifted {
		t.Fatalf("3-sigma latency shift not detected (worst %s psi=%.3f)", feature, psi)
	}
	if feature != "avg_latency_ms" {
		t.Fatalf("worst feature = %s, want avg_latency_ms", feature)
	}
}

func TestDriftDetectorRoundTripAndGuard(t *testing.T) {
	detector, _ := driftFixture(t)
	path := filepath.Join(t.TempDir(), "reference.json")
	if err := detector.Save(path); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadDriftDetector(path)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Edges != detector.Edges || loaded.Reference != detector.Reference {
		t.Fatal("round trip changed the reference")
	}
	detector.FeatureNames[3] = "drifted_name"
	if err := detector.Save(path); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadDriftDetector(path); err == nil {
		t.Fatal("want feature-name guard rejection")
	}
}
