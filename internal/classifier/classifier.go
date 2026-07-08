package classifier

import "polyforge/internal/telemetry"

type Label string

const (
	CRUDBursty       Label = "CRUD-bursty"
	CRUDSteady       Label = "CRUD-steady"
	AICacheable      Label = "AI-cacheable"
	AIUncacheable    Label = "AI-uncacheable"
	AgenticMultistep Label = "agentic-multistep"
	Unknown          Label = "unknown"
)

type Result struct {
	TenantID   string  `json:"tenant_id"`
	Label      Label   `json:"label"`
	Confidence float64 `json:"confidence"`
	Reason     string  `json:"reason"`
}

func Classify(tenantID string, events []telemetry.Event) Result {
	if len(events) == 0 {
		return Result{TenantID: tenantID, Label: Unknown, Confidence: 0.0, Reason: "no telemetry events available"}
	}

	var totalPayload int
	var totalDensity float64
	var totalCacheHits int
	var totalChildSpans int
	var minRPS, maxRPS float64

	minRPS = events[0].RPSWindow
	for _, e := range events {
		totalPayload += e.PayloadBytes
		totalDensity += e.EmbeddingDensity
		totalChildSpans += e.ChildSpans
		if e.CacheHit {
			totalCacheHits++
		}
		if e.RPSWindow < minRPS {
			minRPS = e.RPSWindow
		}
		if e.RPSWindow > maxRPS {
			maxRPS = e.RPSWindow
		}
	}

	n := float64(len(events))
	avgPayload := float64(totalPayload) / n
	avgDensity := totalDensity / n
	cacheRate := float64(totalCacheHits) / n
	avgChildSpans := float64(totalChildSpans) / n
	rpsSpread := maxRPS - minRPS

	if avgChildSpans >= 3 {
		return Result{TenantID: tenantID, Label: AgenticMultistep, Confidence: 0.91, Reason: "average child spans indicate multi-step tool or model calls"}
	}
	if avgPayload > 5000 || avgDensity > 0.45 {
		if cacheRate >= 0.45 || avgDensity >= 0.65 {
			return Result{TenantID: tenantID, Label: AICacheable, Confidence: 0.88, Reason: "large text-like payloads with high embedding density or cache reuse"}
		}
		return Result{TenantID: tenantID, Label: AIUncacheable, Confidence: 0.82, Reason: "large text-like payloads with low cache reuse"}
	}
	if rpsSpread > 75 {
		return Result{TenantID: tenantID, Label: CRUDBursty, Confidence: 0.86, Reason: "small payloads with high RPS variance"}
	}
	return Result{TenantID: tenantID, Label: CRUDSteady, Confidence: 0.8, Reason: "small payloads with stable request rate"}
}
