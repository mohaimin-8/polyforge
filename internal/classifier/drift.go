package classifier

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"sort"
)

// PSIThreshold is the population-stability-index level above which a
// feature's live distribution is considered drifted from training and a
// retrain is due. 0.25 is the conventional "significant shift" bar; 0.1
// is the conventional "watch" bar.
const PSIThreshold = 0.25

const psiBins = 10

// DriftDetector holds the training-time reference distribution of every
// feature as decile bins, and scores live windows against it with PSI.
type DriftDetector struct {
	// Edges[f] are the 9 internal decile cut points of feature f.
	Edges [FeatureCount][psiBins - 1]float64 `json:"edges"`
	// Reference[f] are the training proportions per bin (≈0.1 each by
	// construction, kept exact to be honest about ties).
	Reference    [FeatureCount][psiBins]float64 `json:"reference"`
	FeatureNames [FeatureCount]string           `json:"feature_names"`
}

// NewDriftDetector builds the reference from training examples.
func NewDriftDetector(examples []Example) (*DriftDetector, error) {
	if len(examples) < psiBins {
		return nil, errors.New("classifier: too few examples for a drift reference")
	}
	d := &DriftDetector{FeatureNames: FeatureNames}
	for f := 0; f < FeatureCount; f++ {
		values := make([]float64, len(examples))
		for i, ex := range examples {
			values[i] = ex.Features[f]
		}
		sort.Float64s(values)
		for b := 1; b < psiBins; b++ {
			d.Edges[f][b-1] = values[len(values)*b/psiBins]
		}
		var counts [psiBins]float64
		for _, v := range values {
			counts[d.bin(f, v)]++
		}
		for b := range counts {
			d.Reference[f][b] = counts[b] / float64(len(values))
		}
	}
	return d, nil
}

func (d *DriftDetector) bin(feature int, value float64) int {
	for b, edge := range d.Edges[feature] {
		if value < edge {
			return b
		}
	}
	return psiBins - 1
}

// PSI scores a window of live feature vectors against the reference,
// returning one index per feature: Σ (p−q)·ln(p/q) over the decile bins,
// with ε-smoothing so empty bins stay finite.
func (d *DriftDetector) PSI(recent [][FeatureCount]float64) [FeatureCount]float64 {
	var psi [FeatureCount]float64
	if len(recent) == 0 {
		return psi
	}
	const epsilon = 1e-4
	for f := 0; f < FeatureCount; f++ {
		var counts [psiBins]float64
		for _, vector := range recent {
			counts[d.bin(f, vector[f])]++
		}
		for b := 0; b < psiBins; b++ {
			p := d.Reference[f][b]
			q := counts[b] / float64(len(recent))
			if p < epsilon {
				p = epsilon
			}
			if q < epsilon {
				q = epsilon
			}
			psi[f] += (p - q) * math.Log(p/q)
		}
	}
	return psi
}

// Drifted reports whether any feature crosses the threshold, and the
// worst offender either way.
func (d *DriftDetector) Drifted(recent [][FeatureCount]float64) (bool, string, float64) {
	psi := d.PSI(recent)
	worst := 0
	for f := range psi {
		if psi[f] > psi[worst] {
			worst = f
		}
	}
	return psi[worst] > PSIThreshold, d.FeatureNames[worst], psi[worst]
}

// Save writes the reference artifact next to the model.
func (d *DriftDetector) Save(path string) error {
	raw, err := json.MarshalIndent(d, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(raw, '\n'), 0o644)
}

// LoadDriftDetector reads the reference artifact and refuses index drift.
func LoadDriftDetector(path string) (*DriftDetector, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var d DriftDetector
	if err := json.Unmarshal(raw, &d); err != nil {
		return nil, fmt.Errorf("classifier: parse drift reference %s: %w", path, err)
	}
	if d.FeatureNames != FeatureNames {
		return nil, fmt.Errorf("classifier: drift reference %s built on different features", path)
	}
	return &d, nil
}
