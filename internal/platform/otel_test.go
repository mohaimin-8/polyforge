package platform

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.opentelemetry.io/otel/attribute"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	oteltrace "go.opentelemetry.io/otel/trace"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// TestRequestsEmitOTelServerSpans is the W11 verification: every request
// produces a real SDK span whose name is the low-cardinality route pattern
// and whose attributes carry method, route, and status.
func TestRequestsEmitOTelServerSpans(t *testing.T) {
	recorder := tracetest.NewSpanRecorder()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	defer func() { _ = provider.Shutdown(context.Background()) }()

	tenantStore := tenant.NewStore()
	_, _ = tenantStore.CreateTenant(context.Background(), tenant.Tenant{ID: "alpha", Name: "Alpha"})
	key, _ := tenantStore.CreateAPIKey(context.Background(), "alpha", "k", tenant.ScopeRead)

	server := httptest.NewServer(NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetry.NewStore(100),
		Config{TracerProvider: provider},
	).Handler())
	defer server.Close()

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/tenants/alpha/projects", nil)
	req.Header.Set("X-PolyForge-API-Key", key.Secret)
	req.Header.Set("traceparent", "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}

	spans := recorder.Ended()
	if len(spans) != 1 {
		t.Fatalf("expected exactly one server span, got %d", len(spans))
	}
	span := spans[0]
	if span.Name() != "GET /v1/tenants/{tenant_id}/projects" {
		t.Fatalf("span name must be the route pattern, got %q", span.Name())
	}
	if span.SpanKind() != oteltrace.SpanKindServer {
		t.Fatalf("expected server span kind, got %v", span.SpanKind())
	}
	if got := span.SpanContext().TraceID().String(); got != "4bf92f3577b34da6a3ce929d0e0e4736" {
		t.Fatalf("span did not join the remote trace: %s", got)
	}
	if span.Parent().SpanID().String() != "00f067aa0ba902b7" || !span.Parent().IsRemote() {
		t.Fatalf("span parent is not the remote caller: %+v", span.Parent())
	}

	attrs := make(map[attribute.Key]attribute.Value, len(span.Attributes()))
	for _, kv := range span.Attributes() {
		attrs[kv.Key] = kv.Value
	}
	if attrs["http.route"].AsString() != "/v1/tenants/{tenant_id}/projects" {
		t.Fatalf("missing http.route attribute: %v", attrs)
	}
	if attrs["http.response.status_code"].AsInt64() != http.StatusOK {
		t.Fatalf("missing status attribute: %v", attrs)
	}
	if attrs["polyforge.request_id"].AsString() == "" {
		t.Fatalf("missing request id attribute: %v", attrs)
	}
}
