package planner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

// knownKinds are the request kinds the JCAC system model prices; a feature
// row whose service name is not one of them is counted as generic CRUD
// read traffic. This v1 mapping is deliberately coarse — the classifier's
// WorkloadProfile is the richer signal and refines it in a later
// iteration.
var knownKinds = map[string]bool{
	"crud_read": true, "crud_write": true, "batch": true,
	"chat": true, "embed": true, "agent": true,
}

// featureRow is the subset of the control-plane feature API response the
// planner needs (internal/telemetry.FeatureRow).
type featureRow struct {
	Service      string  `json:"service"`
	EventCount   int     `json:"event_count"`
	AvgRPSWindow float64 `json:"avg_rps_window"`
	AvgLatencyMS float64 `json:"avg_latency_ms"`
	P95LatencyMS float64 `json:"p95_latency_ms"`
}

type featureSet struct {
	Items []featureRow `json:"items"`
	// Truncated means the window matched more than MaxFeatureEvents and the
	// aggregates cover only the earliest slice of it (ADR 0016) — a prefix,
	// not the window. The server has always reported this and the planner
	// never decoded it, so under heavy load the demand estimate was computed
	// from an ever-shorter early slice and *fell* as load rose, silently.
	Truncated bool `json:"truncated"`
	// Since and Until are the window the server actually counted over. It
	// sets Until when it runs the query, so a slow call widens the window
	// past the operator's request; RateFromCount divides by this one.
	Since time.Time `json:"since"`
	Until time.Time `json:"until"`
}

// FeatureDemandSource derives planner demand from the control plane's
// per-tenant feature API (W25a). Token, when set, is sent as a bearer
// credential; the operator uses a read-scope service token. AdminKey takes
// precedence over Token: the operator reads every managed tenant's
// features, which no single tenant-scoped credential can authorize — the
// features endpoint accepts the platform admin key for exactly this.
type FeatureDemandSource struct {
	BaseURL  string
	Token    string
	AdminKey string
	HTTP     *http.Client
	// Window is how far back the demand estimate looks. It must track the
	// control interval: the planner is asked "what is demand *now*", and it
	// forecasts forward from the answer.
	//
	// Leaving this unset made the feature API apply its own
	// DefaultFeatureLookback of 15 minutes, so a 10-second control loop was
	// planning against a 15-minute moving average — roughly 7.5 minutes of
	// phase lag, 45 control intervals. Forecast._trend then took its slope
	// over three windows overlapping by 99.7%, attenuating the very signal
	// the predictive claim rests on. A controller fed a 7.5-minute-lagged
	// average is strictly worse than a reactive one, which is the opposite
	// of what the live plane is meant to demonstrate.
	Window time.Duration
	// RateFromCount computes each kind's rate as event_count / Window
	// instead of the mean of the events' RPSWindow stamps. The stamps come
	// from an in-process RateTracker, so behind N load-balanced pods each
	// one reports about 1/N of the tenant's rate and their mean inherits
	// that; a 16-pod kind run (2026-09-28) read 0.36 rps for a tenant
	// sending 4.1. A shared store (PostgreSQL) holds every pod's events, so
	// the count is exact whatever the pod count; on per-pod SQLite it is not. Opt-in (POLYFORGE_PLANNER_DEMAND_RATE
	// =count): off keeps the signal the registered live campaigns ran on.
	RateFromCount bool
}

// DefaultDemandWindow matches DefaultPlanInterval. Kept as its own constant
// rather than imported to avoid a controllers -> planner import cycle; the
// operator test asserts they agree.
const DefaultDemandWindow = 10 * time.Second

// ParseDemandRate reads POLYFORGE_PLANNER_DEMAND_RATE: "count" turns on
// RateFromCount; "" or "mean" keeps the registered per-pod mean. Anything
// else is an error, so a typo cannot silently keep the attenuated signal.
func ParseDemandRate(mode string) (bool, error) {
	switch mode {
	case "", "mean":
		return false, nil
	case "count":
		return true, nil
	}
	return false, fmt.Errorf("POLYFORGE_PLANNER_DEMAND_RATE must be \"count\", \"mean\" or unset, got %q", mode)
}

func NewFeatureDemandSource(baseURL, token string) *FeatureDemandSource {
	return &FeatureDemandSource{
		BaseURL: baseURL,
		Token:   token,
		HTTP:    &http.Client{Timeout: 3 * time.Second},
		Window:  DefaultDemandWindow,
	}
}

func (f *FeatureDemandSource) TenantDemand(ctx context.Context, tenantID string) (Demand, bool, error) {
	window := f.Window
	if window <= 0 {
		window = DefaultDemandWindow
	}
	since := time.Now().UTC().Add(-window).Format(time.RFC3339Nano)
	endpoint := fmt.Sprintf("%s/v1/tenants/%s/telemetry/features?since=%s",
		f.BaseURL, url.PathEscape(tenantID), url.QueryEscape(since))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return Demand{}, false, err
	}
	if f.AdminKey != "" {
		req.Header.Set("X-PolyForge-Admin-Key", f.AdminKey)
	} else if f.Token != "" {
		req.Header.Set("Authorization", "Bearer "+f.Token)
	}
	resp, err := f.HTTP.Do(req)
	if err != nil {
		return Demand{}, false, fmt.Errorf("feature API unreachable: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode == http.StatusNotFound {
		return Demand{}, false, nil // tenant exists in K8s but not in the control plane yet
	}
	if resp.StatusCode != http.StatusOK {
		return Demand{}, false, fmt.Errorf("feature API returned %d", resp.StatusCode)
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return Demand{}, false, err
	}
	var features featureSet
	if err := json.Unmarshal(body, &features); err != nil {
		return Demand{}, false, fmt.Errorf("decode feature set: %w", err)
	}
	if len(features.Items) == 0 {
		return Demand{}, false, nil
	}
	if features.Truncated {
		// Fail loudly rather than plan on a prefix. The caller treats an
		// error as "no telemetry for this tenant this cycle" and holds the
		// last plan, which is the correct conservative response — far better
		// than scaling *down* because the estimate shrank under load.
		return Demand{}, false, errors.New(
			"feature window truncated: demand would be computed from a " +
				"prefix of the window, not the window")
	}

	countWindow := window
	if !features.Since.IsZero() && features.Until.After(features.Since) {
		countWindow = features.Until.Sub(features.Since)
	}
	demand := Demand{RPS: map[string]float64{}, CrudBaseMs: 50.0}
	var latencyWeight, latencySum float64
	for _, row := range features.Items {
		kind := row.Service
		if !knownKinds[kind] {
			kind = "crud_read"
		}
		rate := row.AvgRPSWindow
		if f.RateFromCount {
			rate = float64(row.EventCount) / countWindow.Seconds()
		}
		demand.RPS[kind] += rate
		latencySum += row.AvgLatencyMS * rate
		latencyWeight += rate
		// Realized p95 per kind: the worst of the rows that fold into the
		// kind (conservative: a smaller headroom is the safe error).
		if row.P95LatencyMS > 0 && rate > 0 {
			if demand.RealizedP95Ms == nil {
				demand.RealizedP95Ms = map[string]float64{}
			}
			if row.P95LatencyMS > demand.RealizedP95Ms[kind] {
				demand.RealizedP95Ms[kind] = row.P95LatencyMS
			}
		}
	}
	if latencyWeight > 0 {
		demand.CrudBaseMs = latencySum / latencyWeight
	}
	return demand, true, nil
}
