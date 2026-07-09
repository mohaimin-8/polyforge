package classifier

import (
	"math"
	"sort"

	"polyforge/internal/telemetry"
)

// FeatureCount is the width of the W26 feature vector. The taxonomy doc
// (research/TAXONOMY.md) defines each feature and the class criteria it
// feeds; the order here is load-bearing — trained models store weights per
// index and refuse to load if the names disagree.
const FeatureCount = 12

// FeatureNames, in vector order.
var FeatureNames = [FeatureCount]string{
	"avg_rps",
	"rps_cv",
	"log_avg_payload_bytes",
	"payload_cv",
	"avg_latency_ms",
	"p95_latency_ms",
	"cache_hit_rate",
	"avg_embedding_density",
	"high_density_fraction",
	"avg_child_spans",
	"multistep_fraction",
	"ai_request_fraction",
}

// Features engineers the 12-dimension vector from one tenant window.
// Everything is derived from the same telemetry.Event stream the feature
// API serves, so the online classifier (W27) and any offline trainer see
// identical inputs by construction.
func Features(events []telemetry.Event) [FeatureCount]float64 {
	var v [FeatureCount]float64
	n := float64(len(events))
	if n == 0 {
		return v
	}
	var rps, payload, latency, density, childSpans []float64
	var cacheHits, highDensity, multistep, aiRequests float64
	for _, e := range events {
		rps = append(rps, e.RPSWindow)
		payload = append(payload, float64(e.PayloadBytes))
		latency = append(latency, e.LatencyMS)
		density = append(density, e.EmbeddingDensity)
		childSpans = append(childSpans, float64(e.ChildSpans))
		if e.CacheHit {
			cacheHits++
		}
		if e.EmbeddingDensity >= 0.5 {
			highDensity++
		}
		if e.ChildSpans >= 3 {
			multistep++
		}
		if e.ModelTier != "" {
			aiRequests++
		}
	}
	meanRPS, cvRPS := meanCV(rps)
	meanPayload, cvPayload := meanCV(payload)
	meanLatency, _ := meanCV(latency)
	meanDensity, _ := meanCV(density)
	meanChild, _ := meanCV(childSpans)

	v[0] = meanRPS
	v[1] = cvRPS
	v[2] = math.Log1p(meanPayload)
	v[3] = cvPayload
	v[4] = meanLatency
	v[5] = percentileOf(latency, 95)
	v[6] = cacheHits / n
	v[7] = meanDensity
	v[8] = highDensity / n
	v[9] = meanChild
	v[10] = multistep / n
	v[11] = aiRequests / n
	return v
}

// meanCV returns the mean and the coefficient of variation (σ/μ, 0 when
// the mean is 0). CV rather than raw variance keeps burstiness comparable
// across tenants of very different scale — a 10→100 RPS swing and a
// 100→1000 swing are the same shape of burst.
func meanCV(values []float64) (mean, cv float64) {
	if len(values) == 0 {
		return 0, 0
	}
	for _, v := range values {
		mean += v
	}
	mean /= float64(len(values))
	if mean == 0 {
		return 0, 0
	}
	var variance float64
	for _, v := range values {
		variance += (v - mean) * (v - mean)
	}
	variance /= float64(len(values))
	return mean, math.Sqrt(variance) / mean
}

// percentileOf mirrors the telemetry package's nearest-rank convention so
// p95_latency_ms means the same thing in the feature API and the model.
func percentileOf(values []float64, p float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	rank := int(math.Ceil(p/100*float64(len(sorted)))) - 1
	if rank < 0 {
		rank = 0
	}
	if rank >= len(sorted) {
		rank = len(sorted) - 1
	}
	return sorted[rank]
}
