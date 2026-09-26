package platform

import (
	"io"
	"log/slog"
	"path/filepath"
	"strings"
	"testing"

	"polyforge/internal/apicontract"
	"polyforge/internal/tenant"
)

// TestOpenAPIContractMatchesRoutes is the OpenAPI-drift gate (gap 4.5d): the
// set of routes the server actually registers must equal the set of
// operations documented in api/openapi.yaml, so a new or renamed endpoint
// cannot ship without updating the contract (and vice versa). Runs in CI as
// a normal `go test`, no external tooling.
//
// Non-API surfaces are excluded explicitly, with a reason, so the exclusion
// is a deliberate documented decision rather than silent drift.
var nonAPIRoutes = map[string]string{
	"GET /admin/workloads": "HTML operator dashboard (W27), not a JSON API operation",
}

func registeredPatterns(t *testing.T) []string {
	t.Helper()
	store := tenant.NewStore()
	srv := NewServer(slog.New(slog.NewTextHandler(io.Discard, nil)), store, nil, Config{AdminKey: "x"})
	return srv.RoutePatterns()
}

func TestOpenAPIContractMatchesRoutes(t *testing.T) {
	specOps, err := apicontract.Operations(filepath.Join("..", "..", "api", "openapi.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var served []string
	for _, p := range registeredPatterns(t) {
		if _, skip := nonAPIRoutes[p]; !skip {
			served = append(served, p)
		}
	}
	undocumented, unimplemented := apicontract.Diff(served, specOps)
	if len(undocumented) > 0 {
		t.Errorf("routes served but NOT in api/openapi.yaml (add them to the spec, "+
			"or to nonAPIRoutes with a reason):\n  %s", strings.Join(undocumented, "\n  "))
	}
	if len(unimplemented) > 0 {
		t.Errorf("operations in api/openapi.yaml with NO served route (remove from the "+
			"spec or implement them):\n  %s", strings.Join(unimplemented, "\n  "))
	}
}
