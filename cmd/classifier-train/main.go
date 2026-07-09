// Command classifier-train fits the W26 workload classifier on the seeded
// synthetic dataset and writes the reproducible artifacts:
//
//	artifacts/classifier-model.json        model weights (loaded by W27)
//	research/results/model_selection.csv   candidate comparison table
//
// Same seeds → byte-identical artifacts. Rerun with real-trace examples
// once the W25b datasets are downloaded (see research/TAXONOMY.md).
package main

import (
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"time"

	"polyforge/internal/classifier"
)

func main() {
	seed := flag.Int64("seed", 1, "dataset seed")
	perClassTrain := flag.Int("train-per-class", 200, "training windows per class")
	perClassTest := flag.Int("test-per-class", 100, "held-out windows per class")
	epochs := flag.Int("epochs", 600, "gradient-descent epochs")
	outDir := flag.String("out", ".", "repository root to write artifacts under")
	flag.Parse()

	train := classifier.SyntheticExamples(rand.New(rand.NewSource(*seed)), *perClassTrain)
	held := classifier.SyntheticExamples(rand.New(rand.NewSource(*seed+1)), *perClassTest)

	model, err := classifier.Train(train, *epochs, 2.0, rand.New(rand.NewSource(*seed+2)))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	candidates := []struct {
		name    string
		predict func([classifier.FeatureCount]float64) classifier.Label
	}{
		{"rules_w19_baseline", predictWithRules},
		{"logreg_softmax_go", func(f [classifier.FeatureCount]float64) classifier.Label {
			label, _ := model.Predict(f)
			return label
		}},
	}

	modelPath := filepath.Join(*outDir, "artifacts", "classifier-model.json")
	csvPath := filepath.Join(*outDir, "research", "results", "model_selection.csv")
	if err := os.MkdirAll(filepath.Dir(csvPath), 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := model.Save(modelPath); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	csv := "model,dataset,accuracy,macro_f1,p_latency_us_per_inference\n"
	for _, candidate := range candidates {
		eval := classifier.Evaluate(candidate.predict, held)
		start := time.Now()
		for i := 0; i < 10000; i++ {
			candidate.predict(held[i%len(held)].Features)
		}
		perCallUS := float64(time.Since(start).Microseconds()) / 10000
		csv += fmt.Sprintf("%s,synthetic_seed%d,%.4f,%.4f,%.2f\n",
			candidate.name, *seed, eval.Accuracy, eval.MacroF1, perCallUS)
		fmt.Printf("%-22s accuracy %.4f  macro-F1 %.4f  %.2fus/inference\n",
			candidate.name, eval.Accuracy, eval.MacroF1, perCallUS)
	}
	if err := os.WriteFile(csvPath, []byte(csv), 0o644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("wrote %s and %s\n", modelPath, csvPath)
}

// predictWithRules adapts the W19 rule baseline to the feature-vector
// interface. The rules read raw aggregates, so the adapter reverses the
// vector's encodings (log payload, fractions) — documented drift risk the
// single Features() implementation otherwise prevents.
func predictWithRules(f [classifier.FeatureCount]float64) classifier.Label {
	if f[9] >= 3 { // avg_child_spans
		return classifier.AgenticMultistep
	}
	avgPayload := math.Expm1(f[2])
	if avgPayload > 5000 || f[7] > 0.45 {
		if f[6] >= 0.45 || f[7] >= 0.65 {
			return classifier.AICacheable
		}
		return classifier.AIUncacheable
	}
	if f[1] > 0.9 { // rps_cv: the CV analogue of the raw 75-RPS spread rule
		return classifier.CRUDBursty
	}
	return classifier.CRUDSteady
}
