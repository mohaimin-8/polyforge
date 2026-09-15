// Package planner is the operator's view of the JCAC planner service
// (services/planner). The transport is JSON over HTTP behind the Client
// interface; ADR 0014 records why (and what a gRPC swap would touch —
// exactly this package, nothing else).
package planner

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Demand is one tenant's observed request mix over the last control window.
//
// RealizedP95Ms is the realized p95 latency per request kind over the same
// window -- the feedback the planner's headroom calibration reads (session
// 48: the controller planned open-loop on its plant model, which predicted
// 3,500 ms where the cluster served 1 ms, and bought replicas that bought
// nothing). Omitted when the window carried no traffic; the planner treats
// an absent map as "nothing observed", never as zero latency.
type Demand struct {
	RPS           map[string]float64 `json:"rps"`
	CrudBaseMs    float64            `json:"crud_base_ms"`
	RealizedP95Ms map[string]float64 `json:"realized_p95_ms,omitempty"`
}

// State mirrors the knobs the Policy CRD controls.
type State struct {
	Replicas int32  `json:"replicas"`
	CacheMB  int32  `json:"cache_mb"`
	Tier     string `json:"tier"`
}

// TenantInput is everything the planner needs to know about one tenant.
type TenantInput struct {
	TenantID        string  `json:"tenant_id"`
	SLOClass        string  `json:"slo_class"`
	HourlyBudgetUSD float64 `json:"hourly_budget_usd"`
	ReplicaMin      int32   `json:"replica_min"`
	ReplicaMax      int32   `json:"replica_max"`
	FairnessWeight  float64 `json:"fairness_weight"`
	// Cache/tier knob bounds mirror the Policy CRD guardrails so the planner
	// optimizes over the same envelope the operator will enforce. All are
	// omitempty: an absent bound means the planner's full-envelope default
	// (no floor / no ceiling / any tier), so a Policy that sets no bound
	// produces a request byte-identical to the pre-bounds one. Setting a
	// knob's min==max pins it — the cache-only / tier-only ablation freeze
	// (PREREG_WAVE4_LIVE_PLANE.md §Arms). CacheMax is a pointer so "no
	// ceiling" (nil) is distinct from a real ceiling of 0.
	CacheMin int32  `json:"cache_min,omitempty"`
	CacheMax *int32 `json:"cache_max,omitempty"`
	TierMin  string `json:"tier_min,omitempty"`
	TierMax  string `json:"tier_max,omitempty"`
	// Interference is the W32 noisy-neighbor score, 0 = clean.
	Interference float64 `json:"interference,omitempty"`
	State        State   `json:"state"`
	Demand       Demand  `json:"demand"`
}

// Weights are the JCAC objective weights (cost, SLO, fairness).
type Weights struct {
	Alpha float64 `json:"alpha"`
	Beta  float64 `json:"beta"`
	Gamma float64 `json:"gamma"`
}

// Limits are cluster-wide resource ceilings shared by all tenants.
type Limits struct {
	CacheMB  int32 `json:"cache_mb"`
	Replicas int32 `json:"replicas"`
}

// Request is one joint planning call covering every managed tenant —
// jointness is the point of JCAC, so there is no per-tenant Plan call.
type Request struct {
	Weights Weights       `json:"weights"`
	Limits  Limits        `json:"limits"`
	Tenants []TenantInput `json:"tenants"`
}

// Plan is the planner's chosen configuration for one tenant.
type Plan struct {
	Replicas           int32   `json:"replicas"`
	CacheMB            int32   `json:"cache_mb"`
	Tier               string  `json:"tier"`
	ProjectedCostUSD   float64 `json:"projected_cost_usd"`
	ProjectedViolation float64 `json:"projected_violation"`
}

// Response is the full joint plan.
type Response struct {
	Solver string          `json:"solver"`
	Plans  map[string]Plan `json:"plans"`
}

// Client is the reconciler-facing interface; tests stub it, production
// uses HTTPClient, and a future gRPC transport implements it identically.
type Client interface {
	Plan(ctx context.Context, req Request) (Response, error)
}

// DemandSource supplies per-tenant demand for planning. The second return
// is false when no telemetry exists for the tenant yet, which excludes it
// from the joint plan rather than planning against zeros.
type DemandSource interface {
	TenantDemand(ctx context.Context, tenantID string) (Demand, bool, error)
}

var validTiers = map[string]bool{"none": true, "small": true, "mid": true, "large": true}

// HTTPClient calls the planner service over HTTP.
type HTTPClient struct {
	BaseURL string
	HTTP    *http.Client
	// AuthToken is the shared bearer token the planner requires on /v1/*
	// (services/planner: --auth-token-file / POLYFORGE_PLANNER_TOKEN).
	// Empty means the planner is running in its unauthenticated research
	// posture; the Helm chart sets it on both sides by default.
	AuthToken string
}

func NewHTTPClient(baseURL string, timeout time.Duration) *HTTPClient {
	if timeout <= 0 {
		timeout = 3 * time.Second
	}
	return &HTTPClient{BaseURL: baseURL, HTTP: &http.Client{Timeout: timeout}}
}

// WithAuthToken sets the bearer token sent on every planner call.
func (c *HTTPClient) WithAuthToken(token string) *HTTPClient {
	c.AuthToken = token
	return c
}

func (c *HTTPClient) Plan(ctx context.Context, req Request) (Response, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return Response{}, fmt.Errorf("encode plan request: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/v1/plan", bytes.NewReader(body))
	if err != nil {
		return Response{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if c.AuthToken != "" {
		httpReq.Header.Set("Authorization", "Bearer "+c.AuthToken)
	}

	resp, err := c.HTTP.Do(httpReq)
	if err != nil {
		return Response{}, fmt.Errorf("planner unreachable: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	payload, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return Response{}, fmt.Errorf("read planner response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return Response{}, fmt.Errorf("planner returned %d: %s", resp.StatusCode, payload)
	}

	var out Response
	if err := json.Unmarshal(payload, &out); err != nil {
		return Response{}, fmt.Errorf("decode planner response: %w", err)
	}
	if err := validate(req, out); err != nil {
		return Response{}, err
	}
	return out, nil
}

// validate rejects malformed plans before they reach the apply path: a
// plan for an unknown tenant, a missing tenant, a bogus tier, or negative
// resources means the planner is unhealthy and the fallback should engage.
func validate(req Request, resp Response) error {
	known := make(map[string]bool, len(req.Tenants))
	for _, t := range req.Tenants {
		known[t.TenantID] = true
	}
	for tid, plan := range resp.Plans {
		if !known[tid] {
			return fmt.Errorf("plan for unrequested tenant %q", tid)
		}
		if !validTiers[plan.Tier] {
			return fmt.Errorf("tenant %q: unknown tier %q", tid, plan.Tier)
		}
		if plan.Replicas < 0 || plan.CacheMB < 0 {
			return fmt.Errorf("tenant %q: negative resources in plan", tid)
		}
	}
	for tid := range known {
		if _, ok := resp.Plans[tid]; !ok {
			return fmt.Errorf("planner omitted tenant %q", tid)
		}
	}
	return nil
}
