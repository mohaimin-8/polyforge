package platform

import (
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// The route label keys two unbounded maps in the metrics registry, one of
// which allocates a 12-bucket histogram per key. So a route label that any
// caller can influence is a remote, unauthenticated memory leak — and it was
// one: routePattern fell back to r.URL.Path whenever ServeMux had not matched,
// which is every rate-limited request (rateLimit answers 429 above the mux)
// and every 404.
//
// Found by the L6 scrape verification, not by a review: 8 of 180 replay
// requests were 429s and appeared under route="/v1/tenants/l6/workloads/replay"
// alongside the 172 under the templated pattern — one route's histogram split
// in two, and one new series per tenant id.

func metricsBody(t *testing.T, baseURL string) string {
	t.Helper()
	resp, err := http.Get(baseURL + "/metrics")
	if err != nil {
		t.Fatalf("scrape: %v", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read metrics: %v", err)
	}
	return string(body)
}

func TestRateLimitedRequestsDoNotMintARouteLabelPerTenant(t *testing.T) {
	// Arrange: a limiter tight enough that most requests are rejected before
	// the mux ever resolves the route.
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test", RateLimit: RateLimitConfig{RequestsPerMinute: 1, Burst: 1}},
	).Handler())
	defer server.Close()

	// Act: the same endpoint for ten different tenant ids.
	for i := 0; i < 10; i++ {
		req, err := http.NewRequest(http.MethodPost,
			fmt.Sprintf("%s/v1/tenants/tenant-%d/workloads/replay", server.URL, i),
			strings.NewReader(`{"kind":"crud_read"}`))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		_ = resp.Body.Close()
	}

	// Assert: no series carries a concrete tenant id.
	body := metricsBody(t, server.URL)
	for i := 0; i < 10; i++ {
		if strings.Contains(body, fmt.Sprintf("tenant-%d", i)) {
			t.Fatalf("a tenant id reached a metric label; that is unbounded cardinality:\n%s",
				firstMatchingLine(body, fmt.Sprintf("tenant-%d", i)))
		}
	}
	// And the rejected requests are still counted, under the real route.
	if !strings.Contains(body, `route="/v1/tenants/{tenant_id}/workloads/replay"`) {
		t.Error("rate-limited requests lost their route entirely; they should carry the templated one")
	}
}

func TestUnmatchedPathsCollapseToASingleLabel(t *testing.T) {
	// Arrange
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	// Act: 40 paths that match nothing — the cheapest cardinality attack
	// there is, and kubectl's /api and /apis probes hit this path by accident.
	for i := 0; i < 40; i++ {
		resp, err := http.Get(fmt.Sprintf("%s/not/a/route/%d", server.URL, i))
		if err != nil {
			t.Fatal(err)
		}
		_ = resp.Body.Close()
	}

	// Assert
	body := metricsBody(t, server.URL)
	if strings.Contains(body, "/not/a/route/") {
		t.Fatalf("an unmatched path reached a metric label:\n%s",
			firstMatchingLine(body, "/not/a/route/"))
	}
	if !strings.Contains(body, `route="`+unmatchedRoute+`"`) {
		t.Errorf("unmatched requests should be counted under %q", unmatchedRoute)
	}
}

func TestRouteLabelsStayBoundedEvenIfSomethingElseMintsThem(t *testing.T) {
	// Arrange: bypass the server and hammer the registry directly, which is
	// what a future middleware labelling with something unbounded would do.
	m := newMetrics()

	// Act
	for i := 0; i < maxRouteLabels*3; i++ {
		m.RecordHTTPRequest(http.MethodGet, fmt.Sprintf("/route/%d", i), 200, time.Millisecond)
	}

	// Assert: the histogram map is what actually costs memory.
	if len(m.httpRequestDuration) > maxRouteLabels+1 {
		t.Fatalf("route labels are unbounded: %d histograms for %d distinct routes",
			len(m.httpRequestDuration), maxRouteLabels*3)
	}
	found := false
	for key := range m.httpRequestDuration {
		if key.Route == overflowRoute {
			found = true
		}
	}
	if !found {
		t.Errorf("labels past the cap should fold into %q so the overflow is visible", overflowRoute)
	}
}

func TestAMatchedRouteStillReportsItsPattern(t *testing.T) {
	// Arrange - the regression guard: the fix must not blunt normal labels.
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenant.NewStore(),
		telemetry.NewStore(100),
		Config{AdminKey: "admin-test"},
	).Handler())
	defer server.Close()

	// Act
	resp, err := http.Get(server.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()

	// Assert
	if body := metricsBody(t, server.URL); !strings.Contains(body, `route="/healthz"`) {
		t.Error("a matched route lost its pattern label")
	}
}

func firstMatchingLine(body, needle string) string {
	for _, line := range strings.Split(body, "\n") {
		if strings.Contains(line, needle) {
			return line
		}
	}
	return ""
}
