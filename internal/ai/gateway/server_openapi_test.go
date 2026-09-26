package gateway_test

import (
	"log/slog"
	"path/filepath"
	"strings"
	"testing"

	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/gateway"
	"polyforge/internal/apicontract"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// TestOpenAPIContractMatchesRoutes is the gateway's OpenAPI-drift gate, the
// control plane's arrangement (internal/platform/server_openapi_test.go): the
// routes the gateway serves must equal the operations in
// api/ai-gateway.openapi.yaml. Audit 2026-09-26 (MEDIUM): the knob API the
// paper's live plane depends on, and every AI route, were in no contract.
func TestOpenAPIContractMatchesRoutes(t *testing.T) {
	specOps, err := apicontract.Operations(filepath.Join("..", "..", "..", "api", "ai-gateway.openapi.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	server := gateway.NewServer(slog.Default(), gateway.Config{
		Provider:  &scriptedProvider{},
		Embedder:  embed.NewLocal(16),
		Tenants:   tenant.NewStore(),
		Telemetry: telemetry.NewStore(0),
	})
	undocumented, unserved := apicontract.Diff(server.RoutePatterns(), specOps)
	if len(undocumented) > 0 {
		t.Errorf("routes served but NOT in api/ai-gateway.openapi.yaml:\n  %s",
			strings.Join(undocumented, "\n  "))
	}
	if len(unserved) > 0 {
		t.Errorf("operations in api/ai-gateway.openapi.yaml with NO served route:\n  %s",
			strings.Join(unserved, "\n  "))
	}
}
