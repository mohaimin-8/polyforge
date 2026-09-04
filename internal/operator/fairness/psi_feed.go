package fairness

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// The production feed for the noisy-neighbour detector, and a correction to
// the premise that named it.
//
// The roadmap called this a "Pixie/Hubble adapter". Neither can supply what
// Sample declares:
//
//   - Hubble observes **network flows**. It carries no CPU stall, no syscall
//     counter and no memory-pressure signal at all, so no Hubble adapter can
//     fill a single field of Sample. Naming it was a category error.
//   - Pixie *can* supply syscall-level data, but only by deploying Vizier
//     into the cluster and querying it in PxL — a second data plane, for one
//     of the three signals.
//
// Two of the three fields are **PSI** (kernel pressure-stall information),
// which the kubelet's cAdvisor already exports per container and which this
// repository already scrapes with Prometheus (`compose.yaml`,
// `deploy/observability/prometheus/`). So the feed that can actually run in
// production is a PromQL feed, and that is what this is.
//
// What it cannot do is honest to state: **there is no syscall rate here.**
// No standard exporter publishes one; it needs eBPF (Pixie, Tetragon,
// Parca). `Queries.Syscalls` is therefore empty by default and the signal
// contributes 0, leaving the detector scoring on two of three axes. Set the
// query if your cluster has such an exporter.
//
// Unit substitution, disclosed: `MemPressureEvents` says *events*, and PSI
// publishes *stalled seconds*. The default query feeds seconds. Detector
// scores every signal as a ratio to the cluster median (`ratioExcess`), and
// a ratio is invariant to the unit — so the substitution is sound for
// scoring while the field name is now imprecise. The one place the unit does
// survive is `ratioExcess`'s epsilon floor: for this signal the floor is 1
// second of stall per window rather than 1 event.
//
// Nothing here runs unless `POLYFORGE_PSI_PROMETHEUS_URL` is set. Without it
// the operator behaves exactly as before, which is what R4 requires of any
// new mechanism.

const (
	// DefaultPSIWindow is the rate window each default query averages over.
	// One minute against a 10 s control interval deliberately overlaps: PSI
	// is noisy per-scrape, and the detector already has its own hysteresis
	// (FlagWindows) for deciding when a loud window means something.
	DefaultPSIWindow = time.Minute
	// DefaultPSIInterval matches the plan loop's cadence, so the detector's
	// window count and the planner's control interval mean the same thing.
	DefaultPSIInterval = 10 * time.Second
	// DefaultTenantLabel is the Prometheus label carrying the tenant id.
	DefaultTenantLabel = "tenant"
	defaultPSITimeout  = 3 * time.Second
	// A vector over a few thousand tenants stays well inside this; the cap
	// exists so a misdirected URL returning a stream cannot exhaust memory.
	// Truncation shows up as a JSON parse error, which fails the poll.
	maxPSIResponseBytes = 8 << 20
)

// PSIQueries are the PromQL expressions behind each Sample field. Each must
// return an instant vector labelled by the tenant label; an empty expression
// means "this cluster cannot supply this signal", and the field stays 0.
//
// The defaults name cAdvisor's PSI series. **Verify them against your own
// cluster before trusting a flag**: PSI export is comparatively new, gated
// per kubelet version, and absent entirely on cgroup v1 nodes. A query that
// matches nothing yields no samples rather than a wrong flag, and Poll says
// so through its returned count.
type PSIQueries struct {
	CPUStall    string
	Syscalls    string
	MemPressure string
}

// DefaultPSIQueries builds the cAdvisor-shaped defaults for one tenant label
// and rate window.
func DefaultPSIQueries(tenantLabel string, window time.Duration) PSIQueries {
	w := strconv.FormatInt(int64(window.Seconds()), 10) + "s"
	return PSIQueries{
		// A share of wall time, so it averages across the tenant's pods:
		// two pods stalled half the window is a half-stalled tenant, not a
		// fully stalled one.
		CPUStall: fmt.Sprintf(
			`avg by (%[1]s) (rate(container_pressure_cpu_stalled_seconds_total{%[1]s!=""}[%[2]s]))`,
			tenantLabel, w),
		// No default: see the syscall note above.
		Syscalls: "",
		// A count-like quantity, so it sums: pressure in two pods is more
		// pressure than in one.
		MemPressure: fmt.Sprintf(
			`sum by (%[1]s) (increase(container_pressure_memory_stalled_seconds_total{%[1]s!=""}[%[2]s]))`,
			tenantLabel, w),
	}
}

// PSIFeed polls Prometheus and drives a Detector. It satisfies
// controller-runtime's Runnable, so the operator adds it to the manager and
// it stops with the manager.
type PSIFeed struct {
	BaseURL     string
	TenantLabel string
	Queries     PSIQueries
	Interval    time.Duration
	HTTP        *http.Client
	Detector    *Detector
	Log         *slog.Logger
	// Token authenticates to a Prometheus behind auth (bearer). Empty is
	// the unauthenticated posture; a query API open to the operator's
	// network is common in-cluster, and is not assumed either way.
	Token string
}

// NewPSIFeed wires a feed with the documented defaults.
func NewPSIFeed(baseURL string, detector *Detector) *PSIFeed {
	return &PSIFeed{
		BaseURL:     baseURL,
		TenantLabel: DefaultTenantLabel,
		Queries:     DefaultPSIQueries(DefaultTenantLabel, DefaultPSIWindow),
		Interval:    DefaultPSIInterval,
		HTTP:        &http.Client{Timeout: defaultPSITimeout},
		Detector:    detector,
	}
}

// Poll runs the configured queries once and returns one Sample per tenant
// that any query reported.
//
// A query that fails aborts the poll rather than contributing zeros: the
// detector scores *relative* to peers, so a tenant silently reported as 0
// while its peers are measured would read as the quietest tenant in the
// cluster — a scraping failure must never look like good behaviour.
func (f *PSIFeed) Poll(ctx context.Context) ([]Sample, error) {
	label := f.tenantLabel()
	byTenant := map[string]*Sample{}

	assign := func(expr string, set func(*Sample, float64)) error {
		if expr == "" {
			return nil
		}
		values, err := f.query(ctx, expr, label)
		if err != nil {
			return err
		}
		for tenant, value := range values {
			sample := byTenant[tenant]
			if sample == nil {
				sample = &Sample{TenantID: tenant}
				byTenant[tenant] = sample
			}
			set(sample, value)
		}
		return nil
	}

	if err := assign(f.Queries.CPUStall, func(s *Sample, v float64) { s.CPUStallShare = v }); err != nil {
		return nil, fmt.Errorf("cpu stall query: %w", err)
	}
	if err := assign(f.Queries.Syscalls, func(s *Sample, v float64) { s.SyscallRate = v }); err != nil {
		return nil, fmt.Errorf("syscall query: %w", err)
	}
	if err := assign(f.Queries.MemPressure, func(s *Sample, v float64) { s.MemPressureEvents = v }); err != nil {
		return nil, fmt.Errorf("memory pressure query: %w", err)
	}

	samples := make([]Sample, 0, len(byTenant))
	for _, sample := range byTenant {
		samples = append(samples, *sample)
	}
	return samples, nil
}

// Start polls on the interval until the context ends. A failed poll is
// logged and skipped: Observe is never called with a partial window, because
// a partial window is exactly what would mislabel a scraping outage as a
// quiet tenant.
func (f *PSIFeed) Start(ctx context.Context) error {
	if f.Detector == nil {
		return fmt.Errorf("psi feed: no detector to observe into")
	}
	interval := f.Interval
	if interval <= 0 {
		interval = DefaultPSIInterval
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			samples, err := f.Poll(ctx)
			if err != nil {
				if f.Log != nil {
					f.Log.Error("psi feed poll", "error", err)
				}
				continue
			}
			if len(samples) == 0 {
				if f.Log != nil {
					f.Log.Warn("psi feed returned no tenants; check the queries and the tenant label",
						"label", f.tenantLabel())
				}
				continue
			}
			f.Detector.Observe(samples)
		}
	}
}

func (f *PSIFeed) tenantLabel() string {
	if f.TenantLabel == "" {
		return DefaultTenantLabel
	}
	return f.TenantLabel
}

// promResponse is the subset of Prometheus's instant-query envelope this
// feed reads. Sample values arrive as strings, which is why they are parsed
// rather than decoded straight into a float.
type promResponse struct {
	Status string `json:"status"`
	Error  string `json:"error"`
	Data   struct {
		ResultType string `json:"resultType"`
		Result     []struct {
			Metric map[string]string `json:"metric"`
			Value  []json.RawMessage `json:"value"`
		} `json:"result"`
	} `json:"data"`
}

func (f *PSIFeed) query(ctx context.Context, expr, label string) (map[string]float64, error) {
	endpoint := fmt.Sprintf("%s/api/v1/query?query=%s", f.BaseURL, url.QueryEscape(expr))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	client := f.HTTP
	if client == nil {
		client = &http.Client{Timeout: defaultPSITimeout}
	}
	if f.Token != "" {
		req.Header.Set("Authorization", "Bearer "+f.Token)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxPSIResponseBytes))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("prometheus %s: %s", resp.Status, truncate(string(body), 200))
	}
	var decoded promResponse
	if err := json.Unmarshal(body, &decoded); err != nil {
		return nil, err
	}
	if decoded.Status != "success" {
		return nil, fmt.Errorf("prometheus reported %q: %s", decoded.Status, decoded.Error)
	}
	if decoded.Data.ResultType != "vector" {
		return nil, fmt.Errorf("expected an instant vector, got %q", decoded.Data.ResultType)
	}

	values := make(map[string]float64, len(decoded.Data.Result))
	for _, series := range decoded.Data.Result {
		tenant := series.Metric[label]
		// A series with no tenant label cannot be attributed. Dropping it is
		// the only safe move: folding it into some tenant would invent
		// interference, and folding it into all of them would shift the
		// median every tenant is scored against.
		if tenant == "" || len(series.Value) != 2 {
			continue
		}
		var raw string
		if err := json.Unmarshal(series.Value[1], &raw); err != nil {
			return nil, fmt.Errorf("tenant %q: sample value is not a string: %w", tenant, err)
		}
		value, err := strconv.ParseFloat(raw, 64)
		if err != nil {
			return nil, fmt.Errorf("tenant %q: %w", tenant, err)
		}
		values[tenant] = value
	}
	return values, nil
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "…"
}
