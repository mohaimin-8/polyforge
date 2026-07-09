package classifier

import (
	"math/rand"
	"path/filepath"
	"testing"
	"time"
)

func trainedModel(t *testing.T) (*Model, []Example) {
	t.Helper()
	trainRng := rand.New(rand.NewSource(1))
	train := SyntheticExamples(trainRng, 120)
	testRng := rand.New(rand.NewSource(2))
	held := SyntheticExamples(testRng, 40)
	model, err := Train(train, 400, 2.0, rand.New(rand.NewSource(3)))
	if err != nil {
		t.Fatalf("Train: %v", err)
	}
	return model, held
}

func TestTrainedModelBeatsQualityBarOnHeldOut(t *testing.T) {
	model, held := trainedModel(t)
	eval := Evaluate(func(f [FeatureCount]float64) Label {
		label, _ := model.Predict(f)
		return label
	}, held)
	if eval.MacroF1 < 0.9 {
		t.Fatalf("held-out macro F1 = %.3f, want >= 0.9 (per-class %+v)", eval.MacroF1, eval.PerClass)
	}
	if eval.Accuracy < 0.9 {
		t.Fatalf("held-out accuracy = %.3f, want >= 0.9", eval.Accuracy)
	}
}

func TestTrainingIsDeterministic(t *testing.T) {
	build := func() *Model {
		train := SyntheticExamples(rand.New(rand.NewSource(1)), 50)
		model, err := Train(train, 100, 2.0, rand.New(rand.NewSource(3)))
		if err != nil {
			t.Fatal(err)
		}
		return model
	}
	first := build()
	second := build()
	if *first != *second {
		t.Fatal("same seeds produced different models")
	}
}

func TestModelSaveLoadRoundTrip(t *testing.T) {
	model, held := trainedModel(t)
	path := filepath.Join(t.TempDir(), "model.json")
	if err := model.Save(path); err != nil {
		t.Fatalf("Save: %v", err)
	}
	loaded, err := LoadModel(path)
	if err != nil {
		t.Fatalf("LoadModel: %v", err)
	}
	for _, ex := range held[:50] {
		wantLabel, wantConf := model.Predict(ex.Features)
		gotLabel, gotConf := loaded.Predict(ex.Features)
		if wantLabel != gotLabel || wantConf != gotConf {
			t.Fatal("round-tripped model predicts differently")
		}
	}
}

func TestLoadModelRejectsFeatureDrift(t *testing.T) {
	model, _ := trainedModel(t)
	model.FeatureNames[0] = "renamed_feature"
	path := filepath.Join(t.TempDir(), "model.json")
	if err := model.Save(path); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadModel(path); err == nil {
		t.Fatal("want feature-drift rejection, got nil")
	}
}

func TestInferenceLatencyBudget(t *testing.T) {
	model, held := trainedModel(t)
	start := time.Now()
	const runs = 10000
	for i := 0; i < runs; i++ {
		model.Predict(held[i%len(held)].Features)
	}
	perCall := time.Since(start) / runs
	// The online budget is 50ms; a linear model should be ~4 orders of
	// magnitude inside it. Assert a loose 1ms so the test never flakes.
	if perCall > time.Millisecond {
		t.Fatalf("inference %s per call, budget 50ms breached at margin", perCall)
	}
}

func TestFeatureVectorShape(t *testing.T) {
	window := SyntheticWindow(rand.New(rand.NewSource(9)), AgenticMultistep)
	features := Features(window)
	if features[9] < 3 {
		t.Fatalf("avg_child_spans = %v for an agentic window", features[9])
	}
	if features[11] == 0 {
		t.Fatal("ai_request_fraction must be positive when model tiers are set")
	}
	var empty [FeatureCount]float64
	if Features(nil) != empty {
		t.Fatal("empty window must produce the zero vector")
	}
}
