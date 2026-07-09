package platform

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// captureEnqueuer records mirrored telemetry events and can simulate a full
// queue.
type captureEnqueuer struct {
	mu     sync.Mutex
	events []telemetry.Event
	reject bool
}

func (c *captureEnqueuer) Enqueue(event telemetry.Event) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.reject {
		return false
	}
	c.events = append(c.events, event)
	return true
}

func (c *captureEnqueuer) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.events)
}

func newTelemetryFixture(t *testing.T, enqueuer AnalyticsEnqueuer) (*httptest.Server, string) {
	t.Helper()
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	if _, err := tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}); err != nil {
		t.Fatal(err)
	}
	key, err := tenantStore.CreateAPIKey(ctx, "acme", "ci", tenant.ScopeFull)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{Analytics: enqueuer},
	).Handler())
	t.Cleanup(server.Close)
	return server, key.Secret
}

func postTelemetry(t *testing.T, server *httptest.Server, secret, body string) *http.Response {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, server.URL+"/v1/telemetry", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-PolyForge-API-Key", secret)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = resp.Body.Close() })
	return resp
}

func TestAcceptedTelemetryIsMirroredToAnalytics(t *testing.T) {
	enqueuer := &captureEnqueuer{}
	server, secret := newTelemetryFixture(t, enqueuer)

	resp := postTelemetry(t, server, secret,
		`{"tenant_id":"acme","service":"api","rps_window":10,"payload_bytes":128,"latency_ms":12.5,"model_tier":"local"}`)
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("ingest status = %d", resp.StatusCode)
	}
	if enqueuer.count() != 1 {
		t.Fatalf("want 1 mirrored event, got %d", enqueuer.count())
	}
	if enqueuer.events[0].TenantID != "acme" || enqueuer.events[0].Service != "api" {
		t.Fatalf("mirrored the wrong event: %+v", enqueuer.events[0])
	}
}

func TestRejectedTelemetryIsNotMirrored(t *testing.T) {
	enqueuer := &captureEnqueuer{}
	server, secret := newTelemetryFixture(t, enqueuer)

	resp := postTelemetry(t, server, secret,
		`{"tenant_id":"acme","service":"api","latency_ms":-5}`)
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("invalid ingest status = %d", resp.StatusCode)
	}
	if enqueuer.count() != 0 {
		t.Fatalf("rejected event must not be mirrored, got %d", enqueuer.count())
	}
}

func TestAnalyticsQueueFullDoesNotAffectIngestResponse(t *testing.T) {
	enqueuer := &captureEnqueuer{reject: true}
	server, secret := newTelemetryFixture(t, enqueuer)

	resp := postTelemetry(t, server, secret,
		`{"tenant_id":"acme","service":"api","rps_window":10,"payload_bytes":128,"latency_ms":12.5}`)
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("a full analytics queue must not fail ingest, status = %d", resp.StatusCode)
	}
}
