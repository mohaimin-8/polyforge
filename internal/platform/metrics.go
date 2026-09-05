package platform

import (
	"fmt"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"polyforge/internal/telemetry"
)

var (
	requestDurationBucketsSec = []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}
	telemetryLatencyBucketsMS = []float64{1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000}
	telemetryPayloadBuckets   = []float64{128, 512, 1024, 4096, 8192, 16384, 65536, 262144, 1048576}
)

type metrics struct {
	mu sync.Mutex

	startedAt time.Time

	// inFlight is the saturation golden signal: requests currently being
	// handled. The other three signals are derivable from the series below
	// (traffic and errors from polyforge_http_requests_total, latency from
	// the duration histogram).
	inFlight int64

	httpRequests        map[httpMetricKey]uint64
	httpRequestDuration map[httpDurationMetricKey]*histogram
	// routeLabels and serviceLabels are the distinct-label sets the caps are
	// enforced on; see the comment above maxRouteLabels for why they exist.
	routeLabels   map[string]struct{}
	serviceLabels map[string]struct{}

	telemetryEvents  map[telemetryMetricKey]uint64
	telemetryLatency map[string]*histogram
	telemetryPayload map[string]*histogram
}

type httpMetricKey struct {
	Method string
	Route  string
	Status int
}

type httpDurationMetricKey struct {
	Method string
	Route  string
}

type telemetryMetricKey struct {
	Service   string
	ModelTier string
	CacheHit  bool
}

type histogram struct {
	buckets []float64
	counts  []uint64
	count   uint64
	sum     float64
}

func newMetrics() *metrics {
	return &metrics{
		startedAt:           time.Now().UTC(),
		httpRequests:        make(map[httpMetricKey]uint64),
		httpRequestDuration: make(map[httpDurationMetricKey]*histogram),
		routeLabels:         make(map[string]struct{}),
		serviceLabels:       make(map[string]struct{}),
		telemetryEvents:     make(map[telemetryMetricKey]uint64),
		telemetryLatency:    make(map[string]*histogram),
		telemetryPayload:    make(map[string]*histogram),
	}
}

func (m *metrics) IncInFlight() {
	if m == nil {
		return
	}
	m.mu.Lock()
	m.inFlight++
	m.mu.Unlock()
}

func (m *metrics) DecInFlight() {
	if m == nil {
		return
	}
	m.mu.Lock()
	m.inFlight--
	m.mu.Unlock()
}

// Every map in this registry is unbounded and several allocate a histogram
// per key, so ANY label value that can be influenced from outside is a remote
// memory leak. Two doors were open, both found in session 43:
//
//   - route: rate-limited and unmatched requests used to be labelled with the
//     raw URL path (server.routePattern, now fixed at the source).
//   - service: `POST /v1/telemetry` carries it in the body and validName only
//     requires 1..200 characters, so one authenticated tenant could allocate
//     two histograms per distinct string — in a shared control plane that is
//     cross-tenant denial of service through the documented ingest path.
//
// The label caps below are the backstop that makes both bounded regardless of
// what the callers do. 256 is generous for a deployment (the server has ~60
// routes; a tenant estate has far fewer than 256 real service names) and the
// overflow label makes the condition visible rather than silent.
const maxRouteLabels = 256

// maxServiceLabels bounds telemetry's client-supplied service dimension.
const maxServiceLabels = 256

// overflowRoute is where labels past a cap are folded. Seeing it in /metrics
// means something is minting labels and should be found.
const overflowRoute = "overflow"

// boundLabel folds a value into overflowRoute once the set is full. Must be
// called with m.mu held.
func boundLabel(seen map[string]struct{}, value string, limit int) string {
	if _, known := seen[value]; known {
		return value
	}
	if len(seen) >= limit {
		return overflowRoute
	}
	seen[value] = struct{}{}
	return value
}

// boundRoute must be called with m.mu held.
func (m *metrics) boundRoute(route string) string {
	return boundLabel(m.routeLabels, route, maxRouteLabels)
}

func (m *metrics) RecordHTTPRequest(method, route string, status int, duration time.Duration) {
	if m == nil {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()

	route = m.boundRoute(route)
	key := httpMetricKey{Method: method, Route: route, Status: status}
	m.httpRequests[key]++

	durationKey := httpDurationMetricKey{Method: method, Route: route}
	h := m.httpRequestDuration[durationKey]
	if h == nil {
		h = newHistogram(requestDurationBucketsSec)
		m.httpRequestDuration[durationKey] = h
	}
	h.Observe(duration.Seconds())
}

func (m *metrics) RecordTelemetryEvent(event telemetry.Event) {
	if m == nil {
		return
	}
	service := strings.TrimSpace(event.Service)
	if service == "" {
		service = "unknown"
	}
	modelTier := strings.TrimSpace(event.ModelTier)
	if modelTier == "" {
		modelTier = "unknown"
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	// The service name comes from the request body; the cap is what keeps a
	// tenant from turning it into unbounded allocation. modelTier needs no
	// cap: validModelTier is an allow-list of five values.
	service = boundLabel(m.serviceLabels, service, maxServiceLabels)

	m.telemetryEvents[telemetryMetricKey{
		Service:   service,
		ModelTier: modelTier,
		CacheHit:  event.CacheHit,
	}]++

	latency := m.telemetryLatency[service]
	if latency == nil {
		latency = newHistogram(telemetryLatencyBucketsMS)
		m.telemetryLatency[service] = latency
	}
	latency.Observe(event.LatencyMS)

	payload := m.telemetryPayload[service]
	if payload == nil {
		payload = newHistogram(telemetryPayloadBuckets)
		m.telemetryPayload[service] = payload
	}
	payload.Observe(float64(event.PayloadBytes))
}

func (m *metrics) WritePrometheus(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	if m == nil {
		w.WriteHeader(http.StatusOK)
		return
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	writeMetricHeader(w, "polyforge_process_start_time_seconds", "gauge", "Unix timestamp when this process started.")
	_, _ = fmt.Fprintf(w, "polyforge_process_start_time_seconds %.0f\n\n", float64(m.startedAt.Unix()))

	writeMetricHeader(w, "polyforge_http_in_flight_requests", "gauge", "Requests currently being handled (saturation golden signal).")
	_, _ = fmt.Fprintf(w, "polyforge_http_in_flight_requests %d\n\n", m.inFlight)

	writeMetricHeader(w, "polyforge_http_requests_total", "counter", "HTTP requests handled by method, route, and status.")
	for _, key := range sortedHTTPKeys(m.httpRequests) {
		_, _ = fmt.Fprintf(
			w,
			"polyforge_http_requests_total{method=%q,route=%q,status=%q} %d\n",
			labelValue(key.Method),
			labelValue(key.Route),
			strconv.Itoa(key.Status),
			m.httpRequests[key],
		)
	}
	_, _ = io.WriteString(w, "\n")

	writeMetricHeader(w, "polyforge_http_request_duration_seconds", "histogram", "HTTP request latency in seconds by method and route.")
	for _, key := range sortedHTTPDurationKeys(m.httpRequestDuration) {
		m.httpRequestDuration[key].WritePrometheus(w, "polyforge_http_request_duration_seconds", map[string]string{
			"method": key.Method,
			"route":  key.Route,
		})
	}

	writeMetricHeader(w, "polyforge_telemetry_events_total", "counter", "Accepted telemetry events by service, model tier, and cache-hit flag.")
	for _, key := range sortedTelemetryKeys(m.telemetryEvents) {
		_, _ = fmt.Fprintf(
			w,
			"polyforge_telemetry_events_total{service=%q,model_tier=%q,cache_hit=%q} %d\n",
			labelValue(key.Service),
			labelValue(key.ModelTier),
			strconv.FormatBool(key.CacheHit),
			m.telemetryEvents[key],
		)
	}
	_, _ = io.WriteString(w, "\n")

	writeMetricHeader(w, "polyforge_telemetry_latency_ms", "histogram", "Accepted telemetry latency values in milliseconds by service.")
	for _, service := range sortedHistogramKeys(m.telemetryLatency) {
		m.telemetryLatency[service].WritePrometheus(w, "polyforge_telemetry_latency_ms", map[string]string{
			"service": service,
		})
	}

	writeMetricHeader(w, "polyforge_telemetry_payload_bytes", "histogram", "Accepted telemetry payload sizes in bytes by service.")
	for _, service := range sortedHistogramKeys(m.telemetryPayload) {
		m.telemetryPayload[service].WritePrometheus(w, "polyforge_telemetry_payload_bytes", map[string]string{
			"service": service,
		})
	}
}

func newHistogram(buckets []float64) *histogram {
	copied := append([]float64(nil), buckets...)
	return &histogram{
		buckets: copied,
		counts:  make([]uint64, len(copied)+1),
	}
}

func (h *histogram) Observe(value float64) {
	h.count++
	h.sum += value
	for i, boundary := range h.buckets {
		if value <= boundary {
			h.counts[i]++
			return
		}
	}
	h.counts[len(h.counts)-1]++
}

func (h *histogram) WritePrometheus(w io.Writer, name string, labels map[string]string) {
	var cumulative uint64
	for i, boundary := range h.buckets {
		cumulative += h.counts[i]
		_, _ = fmt.Fprintf(w, "%s_bucket{%sle=%q} %d\n", name, formatLabels(labels), formatBoundary(boundary), cumulative)
	}
	cumulative += h.counts[len(h.counts)-1]
	_, _ = fmt.Fprintf(w, "%s_bucket{%sle=%q} %d\n", name, formatLabels(labels), "+Inf", cumulative)
	_, _ = fmt.Fprintf(w, "%s_sum{%s} %s\n", name, strings.TrimSuffix(formatLabels(labels), ","), formatFloat(h.sum))
	_, _ = fmt.Fprintf(w, "%s_count{%s} %d\n\n", name, strings.TrimSuffix(formatLabels(labels), ","), h.count)
}

func writeMetricHeader(w io.Writer, name, metricType, help string) {
	_, _ = fmt.Fprintf(w, "# HELP %s %s\n# TYPE %s %s\n", name, help, name, metricType)
}

func formatLabels(labels map[string]string) string {
	keys := make([]string, 0, len(labels))
	for key := range labels {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var b strings.Builder
	for _, key := range keys {
		b.WriteString(key)
		b.WriteString("=")
		b.WriteString(strconv.Quote(labelValue(labels[key])))
		b.WriteString(",")
	}
	return b.String()
}

func labelValue(value string) string {
	return value
}

func formatBoundary(value float64) string {
	return strconv.FormatFloat(value, 'f', -1, 64)
}

func formatFloat(value float64) string {
	return strconv.FormatFloat(value, 'f', -1, 64)
}

func sortedHTTPKeys(values map[httpMetricKey]uint64) []httpMetricKey {
	keys := make([]httpMetricKey, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].Method != keys[j].Method {
			return keys[i].Method < keys[j].Method
		}
		if keys[i].Route != keys[j].Route {
			return keys[i].Route < keys[j].Route
		}
		return keys[i].Status < keys[j].Status
	})
	return keys
}

func sortedHTTPDurationKeys(values map[httpDurationMetricKey]*histogram) []httpDurationMetricKey {
	keys := make([]httpDurationMetricKey, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].Method != keys[j].Method {
			return keys[i].Method < keys[j].Method
		}
		return keys[i].Route < keys[j].Route
	})
	return keys
}

func sortedTelemetryKeys(values map[telemetryMetricKey]uint64) []telemetryMetricKey {
	keys := make([]telemetryMetricKey, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].Service != keys[j].Service {
			return keys[i].Service < keys[j].Service
		}
		if keys[i].ModelTier != keys[j].ModelTier {
			return keys[i].ModelTier < keys[j].ModelTier
		}
		return !keys[i].CacheHit && keys[j].CacheHit
	})
	return keys
}

func sortedHistogramKeys(values map[string]*histogram) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
