package classifier

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/rand"
	"os"
)

// Classes in model output order.
var Classes = [5]Label{CRUDBursty, CRUDSteady, AICacheable, AIUncacheable, AgenticMultistep}

// Model is a multinomial logistic regression over the 12-feature vector.
//
// Deliberate deviation from the roadmap's ONNX/XGBoost sketch (ADR 0012):
// ONNX Runtime in Go means cgo and a native library on every deploy
// target; a softmax linear model is dependency-free, interpretable per
// feature (the paper's ablation table falls out of the weight matrix),
// and classifies in microseconds against a 50ms budget. The gradient
// boosting / LSTM comparison belongs to the real-trace phase and is
// tracked as pending in research/TAXONOMY.md.
type Model struct {
	// Mean/Std standardize features before the linear layer.
	Mean [FeatureCount]float64 `json:"mean"`
	Std  [FeatureCount]float64 `json:"std"`
	// Weights is [class][feature]; Bias is per class.
	Weights [len(Classes)][FeatureCount]float64 `json:"weights"`
	Bias    [len(Classes)]float64               `json:"bias"`
	// FeatureNames and ClassNames guard against index drift between the
	// trained artifact and the code that loads it.
	FeatureNames [FeatureCount]string `json:"feature_names"`
	ClassNames   [len(Classes)]string `json:"class_names"`
}

// Predict returns the argmax class and its softmax probability.
func (m *Model) Predict(features [FeatureCount]float64) (Label, float64) {
	probs := m.probabilities(features)
	best := 0
	for i := range probs {
		if probs[i] > probs[best] {
			best = i
		}
	}
	return Classes[best], probs[best]
}

func (m *Model) probabilities(features [FeatureCount]float64) [len(Classes)]float64 {
	var logits [len(Classes)]float64
	for c := range Classes {
		logits[c] = m.Bias[c]
		for f := 0; f < FeatureCount; f++ {
			std := m.Std[f]
			if std == 0 {
				std = 1
			}
			logits[c] += m.Weights[c][f] * ((features[f] - m.Mean[f]) / std)
		}
	}
	maxLogit := logits[0]
	for _, l := range logits[1:] {
		if l > maxLogit {
			maxLogit = l
		}
	}
	var sum float64
	var probs [len(Classes)]float64
	for c := range logits {
		probs[c] = math.Exp(logits[c] - maxLogit)
		sum += probs[c]
	}
	for c := range probs {
		probs[c] /= sum
	}
	return probs
}

// Example is one labeled training window.
type Example struct {
	Features [FeatureCount]float64
	Label    Label
}

// Train fits the model by full-batch gradient descent on cross-entropy.
// Deterministic: iteration order is fixed and the only randomness is the
// caller-provided rng used for weight init.
func Train(examples []Example, epochs int, learningRate float64, rng *rand.Rand) (*Model, error) {
	if len(examples) == 0 {
		return nil, errors.New("classifier: no training examples")
	}
	classIndex := map[Label]int{}
	for i, c := range Classes {
		classIndex[c] = i
	}
	model := &Model{FeatureNames: FeatureNames}
	for i, c := range Classes {
		model.ClassNames[i] = string(c)
	}
	// Standardization statistics from the training set.
	for f := 0; f < FeatureCount; f++ {
		var mean float64
		for _, ex := range examples {
			mean += ex.Features[f]
		}
		mean /= float64(len(examples))
		var variance float64
		for _, ex := range examples {
			variance += (ex.Features[f] - mean) * (ex.Features[f] - mean)
		}
		model.Mean[f] = mean
		model.Std[f] = math.Sqrt(variance / float64(len(examples)))
	}
	for c := range Classes {
		for f := 0; f < FeatureCount; f++ {
			model.Weights[c][f] = rng.NormFloat64() * 0.01
		}
	}
	standardized := make([][FeatureCount]float64, len(examples))
	targets := make([]int, len(examples))
	for i, ex := range examples {
		idx, ok := classIndex[ex.Label]
		if !ok {
			return nil, fmt.Errorf("classifier: unknown label %q", ex.Label)
		}
		targets[i] = idx
		for f := 0; f < FeatureCount; f++ {
			std := model.Std[f]
			if std == 0 {
				std = 1
			}
			standardized[i][f] = (ex.Features[f] - model.Mean[f]) / std
		}
	}
	n := float64(len(examples))
	for epoch := 0; epoch < epochs; epoch++ {
		var gradW [len(Classes)][FeatureCount]float64
		var gradB [len(Classes)]float64
		for i := range standardized {
			// Inline forward pass on standardized features.
			var logits [len(Classes)]float64
			for c := range Classes {
				logits[c] = model.Bias[c]
				for f := 0; f < FeatureCount; f++ {
					logits[c] += model.Weights[c][f] * standardized[i][f]
				}
			}
			maxLogit := logits[0]
			for _, l := range logits[1:] {
				if l > maxLogit {
					maxLogit = l
				}
			}
			var sum float64
			var probs [len(Classes)]float64
			for c := range logits {
				probs[c] = math.Exp(logits[c] - maxLogit)
				sum += probs[c]
			}
			for c := range probs {
				probs[c] /= sum
				indicator := 0.0
				if targets[i] == c {
					indicator = 1.0
				}
				delta := (probs[c] - indicator) / n
				gradB[c] += delta
				for f := 0; f < FeatureCount; f++ {
					gradW[c][f] += delta * standardized[i][f]
				}
			}
		}
		for c := range Classes {
			model.Bias[c] -= learningRate * gradB[c]
			for f := 0; f < FeatureCount; f++ {
				model.Weights[c][f] -= learningRate * gradW[c][f]
			}
		}
	}
	return model, nil
}

// Save writes the model artifact as indented JSON.
func (m *Model) Save(path string) error {
	raw, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(raw, '\n'), 0o644)
}

// LoadModel reads a model artifact and refuses index drift.
func LoadModel(path string) (*Model, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var model Model
	if err := json.Unmarshal(raw, &model); err != nil {
		return nil, fmt.Errorf("classifier: parse model %s: %w", path, err)
	}
	if model.FeatureNames != FeatureNames {
		return nil, fmt.Errorf("classifier: model %s was trained on different features", path)
	}
	for i, c := range Classes {
		if model.ClassNames[i] != string(c) {
			return nil, fmt.Errorf("classifier: model %s has class order %v", path, model.ClassNames)
		}
	}
	return &model, nil
}
