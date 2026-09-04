package fairness

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

// promStub answers /api/v1/query from a table of expression-prefix -> vector,
// and records what it was asked. Matching on a prefix keeps the tests
// readable without pinning the exact PromQL text, which is configurable.
type promStub struct {
	mu       sync.Mutex
	asked    []string
	vectors  map[string]map[string]float64
	status   int
	failWith string
}

func newPromStub() *promStub {
	return &promStub{vectors: map[string]map[string]float64{}, status: http.StatusOK}
}

func (p *promStub) serve() *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		expr := r.URL.Query().Get("query")
		p.mu.Lock()
		p.asked = append(p.asked, expr)
		p.mu.Unlock()

		if p.status != http.StatusOK {
			w.WriteHeader(p.status)
			_, _ = w.Write([]byte("boom"))
			return
		}
		if p.failWith != "" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "error", "errorType": "bad_data", "error": p.failWith,
			})
			return
		}
		result := []map[string]any{}
		for key, byTenant := range p.vectors {
			if !strings.Contains(expr, key) {
				continue
			}
			for tenant, value := range byTenant {
				result = append(result, map[string]any{
					"metric": map[string]string{"tenant": tenant},
					"value":  []any{1.0, fmt.Sprintf("%v", value)},
				})
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "success",
			"data":   map[string]any{"resultType": "vector", "result": result},
		})
	}))
}

func (p *promStub) questions() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string(nil), p.asked...)
}

func TestPSIFeedMapsEachQueryToItsSampleField(t *testing.T) {
	stub := newPromStub()
	stub.vectors["cpu_stalled"] = map[string]float64{"noisy": 0.4, "quiet": 0.01}
	stub.vectors["memory_stalled"] = map[string]float64{"noisy": 12, "quiet": 1}
	stub.vectors["syscalls_total"] = map[string]float64{"noisy": 9000}
	server := stub.serve()
	defer server.Close()

	feed := NewPSIFeed(server.URL, NewDetector(DetectorConfig{}))
	feed.Queries.Syscalls = `sum by (tenant) (rate(syscalls_total[1m]))`

	samples, err := feed.Poll(context.Background())
	if err != nil {
		t.Fatalf("poll: %v", err)
	}
	if len(samples) != 2 {
		t.Fatalf("want 2 tenants, got %d (%+v)", len(samples), samples)
	}
	got := map[string]Sample{}
	for _, s := range samples {
		got[s.TenantID] = s
	}
	if got["noisy"].CPUStallShare != 0.4 || got["noisy"].MemPressureEvents != 12 ||
		got["noisy"].SyscallRate != 9000 {
		t.Errorf("noisy tenant mapped wrong: %+v", got["noisy"])
	}
	// The quiet tenant appears in two of three vectors; the third stays zero
	// rather than dropping the tenant from the window.
	if got["quiet"].CPUStallShare != 0.01 || got["quiet"].SyscallRate != 0 {
		t.Errorf("quiet tenant mapped wrong: %+v", got["quiet"])
	}
}

func TestPSIFeedSkipsAnUnconfiguredSignalWithoutQueryingForIt(t *testing.T) {
	stub := newPromStub()
	stub.vectors["cpu_stalled"] = map[string]float64{"a": 0.2}
	server := stub.serve()
	defer server.Close()

	// The default Syscalls query is empty: no exporter publishes one.
	feed := NewPSIFeed(server.URL, NewDetector(DetectorConfig{}))
	if feed.Queries.Syscalls != "" {
		t.Fatalf("default syscall query should be empty, got %q", feed.Queries.Syscalls)
	}
	if _, err := feed.Poll(context.Background()); err != nil {
		t.Fatalf("poll: %v", err)
	}
	if len(stub.questions()) != 2 {
		t.Errorf("want 2 queries (cpu, memory), got %d: %v", len(stub.questions()), stub.questions())
	}
}

func TestPSIFeedFailsThePollRatherThanReportingAScrapeOutageAsQuiet(t *testing.T) {
	// A tenant reported as 0 while its peers are measured reads as the
	// quietest in the cluster, because the detector scores against the peer
	// median. Failing loudly is the only safe behaviour.
	stub := newPromStub()
	stub.status = http.StatusServiceUnavailable
	server := stub.serve()
	defer server.Close()

	feed := NewPSIFeed(server.URL, NewDetector(DetectorConfig{}))
	samples, err := feed.Poll(context.Background())
	if err == nil {
		t.Fatalf("want an error, got samples %+v", samples)
	}
	if samples != nil {
		t.Errorf("a failed poll must yield no samples, got %+v", samples)
	}
}

func TestPSIFeedSurfacesAPrometheusErrorEnvelope(t *testing.T) {
	stub := newPromStub()
	stub.failWith = "parse error at char 7"
	server := stub.serve()
	defer server.Close()

	feed := NewPSIFeed(server.URL, NewDetector(DetectorConfig{}))
	_, err := feed.Poll(context.Background())
	if err == nil || !strings.Contains(err.Error(), "parse error") {
		t.Fatalf("want the upstream message, got %v", err)
	}
}

func TestPSIFeedDropsASeriesThatCarriesNoTenantLabel(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "success",
			"data": map[string]any{"resultType": "vector", "result": []map[string]any{
				{"metric": map[string]string{"tenant": "a"}, "value": []any{1.0, "0.5"}},
				{"metric": map[string]string{"pod": "orphan"}, "value": []any{1.0, "99"}},
			}},
		})
	}))
	defer server.Close()

	feed := NewPSIFeed(server.URL, NewDetector(DetectorConfig{}))
	feed.Queries = PSIQueries{CPUStall: "anything"}
	samples, err := feed.Poll(context.Background())
	if err != nil {
		t.Fatalf("poll: %v", err)
	}
	// Attributing it to a tenant would invent interference; spreading it
	// across all of them would move the median everyone is scored against.
	if len(samples) != 1 || samples[0].TenantID != "a" {
		t.Fatalf("want only the labelled series, got %+v", samples)
	}
}

func TestPSIFeedRejectsARangeVector(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "success",
			"data":   map[string]any{"resultType": "matrix", "result": []map[string]any{}},
		})
	}))
	defer server.Close()

	feed := NewPSIFeed(server.URL, NewDetector(DetectorConfig{}))
	feed.Queries = PSIQueries{CPUStall: "rate(x[5m])[1h:]"}
	if _, err := feed.Poll(context.Background()); err == nil {
		t.Fatal("a matrix result must be rejected, not silently ignored")
	}
}

func TestPSIFeedRunsUntilTheContextEndsAndFlagsAPersistentlyNoisyTenant(t *testing.T) {
	stub := newPromStub()
	stub.vectors["cpu_stalled"] = map[string]float64{"noisy": 0.9, "q1": 0.01, "q2": 0.01}
	stub.vectors["memory_stalled"] = map[string]float64{"noisy": 40, "q1": 1, "q2": 1}
	server := stub.serve()
	defer server.Close()

	detector := NewDetector(DetectorConfig{FlagWindows: 3})
	feed := NewPSIFeed(server.URL, detector)
	feed.Interval = time.Millisecond

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- feed.Start(ctx) }()

	deadline := time.After(2 * time.Second)
	for {
		if flagged := detector.Flagged(); len(flagged) == 1 && flagged[0] == "noisy" {
			break
		}
		select {
		case <-deadline:
			t.Fatalf("tenant never flagged; flagged=%v", detector.Flagged())
		case <-time.After(5 * time.Millisecond):
		}
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("Start returned %v", err)
	}
}

func TestPSIFeedQueriesAreEscapedIntoTheURL(t *testing.T) {
	// The default expressions contain braces, quotes and brackets; an
	// unescaped query would reach Prometheus mangled or not at all.
	var raw string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw = r.URL.RawQuery
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "success",
			"data":   map[string]any{"resultType": "vector", "result": []map[string]any{}},
		})
	}))
	defer server.Close()

	feed := NewPSIFeed(server.URL, NewDetector(DetectorConfig{}))
	feed.Queries = PSIQueries{CPUStall: DefaultPSIQueries("tenant", time.Minute).CPUStall}
	if _, err := feed.Poll(context.Background()); err != nil {
		t.Fatalf("poll: %v", err)
	}
	values, err := url.ParseQuery(raw)
	if err != nil {
		t.Fatalf("the query string did not parse: %v", err)
	}
	if got := values.Get("query"); got != feed.Queries.CPUStall {
		t.Errorf("query round-tripped wrong:\n got %q\nwant %q", got, feed.Queries.CPUStall)
	}
}

func TestDefaultQueriesCarryTheConfiguredLabelAndWindow(t *testing.T) {
	queries := DefaultPSIQueries("tenant_id", 30*time.Second)
	for name, expr := range map[string]string{
		"cpu": queries.CPUStall, "memory": queries.MemPressure,
	} {
		if !strings.Contains(expr, "tenant_id") {
			t.Errorf("%s query ignores the tenant label: %s", name, expr)
		}
		if !strings.Contains(expr, "[30s]") {
			t.Errorf("%s query ignores the window: %s", name, expr)
		}
	}
	if queries.Syscalls != "" {
		t.Errorf("no standard exporter publishes a syscall rate; want empty, got %q", queries.Syscalls)
	}
}

func TestPSIFeedSendsItsBearerTokenWhenOneIsConfigured(t *testing.T) {
	var auth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth = r.Header.Get("Authorization")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "success",
			"data":   map[string]any{"resultType": "vector", "result": []map[string]any{}},
		})
	}))
	defer server.Close()

	feed := NewPSIFeed(server.URL, NewDetector(DetectorConfig{}))
	feed.Token = "s3cret"
	if _, err := feed.Poll(context.Background()); err != nil {
		t.Fatalf("poll: %v", err)
	}
	if auth != "Bearer s3cret" {
		t.Errorf("want a bearer header, got %q", auth)
	}
}

func TestPSIFeedWithoutADetectorFailsAtStartupNotOnTheFirstTick(t *testing.T) {
	// Discovering this on the first tick means finding it in production,
	// seconds after a rollout looked healthy.
	feed := &PSIFeed{BaseURL: "http://unused", Interval: time.Millisecond}
	if err := feed.Start(context.Background()); err == nil {
		t.Fatal("a feed with no detector must refuse to start")
	}
}
