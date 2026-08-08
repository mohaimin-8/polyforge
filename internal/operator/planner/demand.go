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
	AvgRPSWindow float64 `json:"avg_rps_window"`
	AvgLatencyMS float64 `json:"avg_latency_ms"`
}

type featureSet struct {
	Items []featureRow `json:"items"`
	// Truncated means the window matched more than MaxFeatureEvents and the
	// aggregates cover only the earliest slice of it (ADR 0016) — a prefix,
	// not the window. The server has always reported this and the planner
	// never decoded it, so under heavy load the demand estimate was computed
	// from an ever-shorter early slice and *fell* as load rose, silently.
	Truncated bool `json:"truncated"`
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
}

// DefaultDemandWindow matches DefaultPlanInterval. Kept as its own constant
// rather than imported to avoid a controllers -> planner import cycle; the
// operator test asserts they agree.
const DefaultDemandWindow = 10 * time.Second

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

	demand := Demand{RPS: map[string]float64{}, CrudBaseMs: 50.0}
	var latencyWeight, latencySum float64
	for _, row := range features.Items {
		kind := row.Service
		if !knownKinds[kind] {
			kind = "crud_read"
		}
		demand.RPS[kind] += row.AvgRPSWindow
		latencySum += row.AvgLatencyMS * row.AvgRPSWindow
		latencyWeight += row.AvgRPSWindow
	}
	if latencyWeight > 0 {
		demand.CrudBaseMs = latencySum / latencyWeight
	}
	return demand, true, nil
}
