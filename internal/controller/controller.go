package controller

import "polyforge/internal/classifier"

type Recommendation struct {
	TenantID     string           `json:"tenant_id"`
	Workload     classifier.Label `json:"workload"`
	Replicas     int              `json:"replicas"`
	CacheMB      int              `json:"cache_mb"`
	ModelTier    string           `json:"model_tier"`
	FairnessMode string           `json:"fairness_mode"`
	Reason       string           `json:"reason"`
}

func Recommend(result classifier.Result) Recommendation {
	rec := Recommendation{
		TenantID:     result.TenantID,
		Workload:     result.Label,
		Replicas:     2,
		CacheMB:      128,
		ModelTier:    "small",
		FairnessMode: "shared",
	}

	switch result.Label {
	case classifier.CRUDBursty:
		rec.Replicas = 5
		rec.CacheMB = 128
		rec.ModelTier = "none"
		rec.Reason = "bursting CRUD traffic benefits first from replica headroom"
	case classifier.CRUDSteady:
		rec.Replicas = 2
		rec.CacheMB = 96
		rec.ModelTier = "none"
		rec.Reason = "steady CRUD traffic can stay cost-efficient with modest replicas"
	case classifier.AICacheable:
		rec.Replicas = 3
		rec.CacheMB = 1024
		rec.ModelTier = "mid"
		rec.Reason = "cacheable AI traffic benefits from larger semantic cache and moderate model tier"
	case classifier.AIUncacheable:
		rec.Replicas = 4
		rec.CacheMB = 256
		rec.ModelTier = "large"
		rec.Reason = "uncacheable AI traffic needs model quality and compute more than cache"
	case classifier.AgenticMultistep:
		rec.Replicas = 5
		rec.CacheMB = 512
		rec.ModelTier = "mid"
		rec.FairnessMode = "guarded"
		rec.Reason = "agentic workloads create fan-out and must be isolated from neighbors"
	default:
		rec.Reason = "insufficient telemetry; using conservative defaults"
	}

	return rec
}
