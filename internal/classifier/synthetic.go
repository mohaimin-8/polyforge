package classifier

import (
	"math/rand"
	"time"

	"polyforge/internal/telemetry"
)

// SyntheticWindow fabricates one tenant window whose statistics satisfy
// the TAXONOMY criteria for the label — contaminated with a per-window
// fraction (up to 30%) of events drawn from *other* classes, because real
// tenants run mixed workloads and a classifier that only separates pure
// windows is not worth benchmarking. This is supervision for pipeline
// validation and the trainer's unit economy — real-trace windows labeled
// via the replay driver supersede it for every number that reaches the
// paper (research/TAXONOMY.md, "Labeling protocol").
func SyntheticWindow(rng *rand.Rand, label Label) []telemetry.Event {
	n := 40 + rng.Intn(60)
	contamination := rng.Float64() * 0.3
	events := make([]telemetry.Event, n)
	base := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	for i := range events {
		effective := label
		if rng.Float64() < contamination {
			effective = Classes[rng.Intn(len(Classes))]
		}
		events[i] = eventFor(rng, effective)
		events[i].Timestamp = base.Add(time.Duration(i) * 9 * time.Second)
	}
	return events
}

func eventFor(rng *rand.Rand, label Label) telemetry.Event {
	e := telemetry.Event{TenantID: "synthetic", Service: "api"}
	switch label {
	case CRUDBursty:
		// Small payloads; rate flips between idle and spike (high CV).
		e.PayloadBytes = 200 + rng.Intn(2000)
		if rng.Float64() < 0.25 {
			e.RPSWindow = 150 + rng.Float64()*250
		} else {
			e.RPSWindow = 2 + rng.Float64()*10
		}
		e.LatencyMS = 5 + rng.Float64()*20
		e.CacheHit = rng.Float64() < 0.05
	case CRUDSteady:
		e.PayloadBytes = 200 + rng.Intn(2000)
		e.RPSWindow = 40 + rng.Float64()*10
		e.LatencyMS = 5 + rng.Float64()*15
		e.CacheHit = rng.Float64() < 0.05
	case AICacheable:
		e.PayloadBytes = 2000 + rng.Intn(9000)
		e.RPSWindow = 5 + rng.Float64()*15
		e.LatencyMS = 300 + rng.Float64()*1500
		e.EmbeddingDensity = 0.45 + rng.Float64()*0.5
		e.CacheHit = rng.Float64() < 0.35+rng.Float64()*0.3
		e.ModelTier = "local"
	case AIUncacheable:
		e.PayloadBytes = 2000 + rng.Intn(9000)
		e.RPSWindow = 5 + rng.Float64()*15
		e.LatencyMS = 500 + rng.Float64()*2500
		e.EmbeddingDensity = 0.05 + rng.Float64()*0.45
		e.CacheHit = rng.Float64() < 0.08
		e.ModelTier = "quality"
	case AgenticMultistep:
		e.PayloadBytes = 1000 + rng.Intn(8000)
		e.RPSWindow = 1 + rng.Float64()*6
		e.LatencyMS = 2000 + rng.Float64()*8000
		e.EmbeddingDensity = 0.2 + rng.Float64()*0.4
		e.ChildSpans = 3 + rng.Intn(4)
		e.ModelTier = "quality"
		e.CacheHit = rng.Float64() < 0.1
	}
	return e
}

// SyntheticExamples builds a balanced labeled feature dataset.
func SyntheticExamples(rng *rand.Rand, perClass int) []Example {
	var examples []Example
	for _, label := range Classes {
		for i := 0; i < perClass; i++ {
			examples = append(examples, Example{
				Features: Features(SyntheticWindow(rng, label)),
				Label:    label,
			})
		}
	}
	rng.Shuffle(len(examples), func(i, j int) {
		examples[i], examples[j] = examples[j], examples[i]
	})
	return examples
}
