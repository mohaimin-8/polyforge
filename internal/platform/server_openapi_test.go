package platform

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

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

// parseOpenAPIOperations reads the "METHOD /path" operations from the spec.
// A tiny structural scan (not a full YAML parse) keeps the test
// dependency-free: paths live under a top-level "paths:" map as "  /x:"
// keys, each followed by method keys ("    get:", "    post:", …).
func parseOpenAPIOperations(t *testing.T) []string {
	t.Helper()
	repoRoot, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(repoRoot, "api", "openapi.yaml"))
	if err != nil {
		t.Fatalf("read openapi.yaml: %v", err)
	}
	lines := strings.Split(string(data), "\n")

	pathRE := regexp.MustCompile(`^ {2}(/\S*):\s*$`)
	methodRE := regexp.MustCompile(`^ {4}(get|post|put|patch|delete):\s*$`)

	var ops []string
	inPaths := false
	curPath := ""
	for _, line := range lines {
		if strings.HasPrefix(line, "paths:") {
			inPaths = true
			continue
		}
		if !inPaths {
			continue
		}
		// A new top-level key ends the paths section.
		if len(line) > 0 && line[0] != ' ' && strings.TrimSpace(line) != "" {
			break
		}
		if m := pathRE.FindStringSubmatch(line); m != nil {
			curPath = m[1]
			continue
		}
		if m := methodRE.FindStringSubmatch(line); m != nil && curPath != "" {
			ops = append(ops, strings.ToUpper(m[1])+" "+curPath)
		}
	}
	return ops
}

func TestOpenAPIContractMatchesRoutes(t *testing.T) {
	specOps := parseOpenAPIOperations(t)
	if len(specOps) == 0 {
		t.Fatal("parsed zero operations from openapi.yaml — the scanner or the spec changed shape")
	}

	spec := map[string]bool{}
	for _, op := range specOps {
		spec[op] = true
	}
	served := map[string]bool{}
	for _, p := range registeredPatterns(t) {
		if _, skip := nonAPIRoutes[p]; skip {
			continue
		}
		served[p] = true
	}

	var undocumented, unimplemented []string
	for op := range served {
		if !spec[op] {
			undocumented = append(undocumented, op)
		}
	}
	for op := range spec {
		if !served[op] {
			unimplemented = append(unimplemented, op)
		}
	}
	sort.Strings(undocumented)
	sort.Strings(unimplemented)

	if len(undocumented) > 0 {
		t.Errorf("routes served but NOT in api/openapi.yaml (add them to the spec, "+
			"or to nonAPIRoutes with a reason):\n  %s", strings.Join(undocumented, "\n  "))
	}
	if len(unimplemented) > 0 {
		t.Errorf("operations in api/openapi.yaml with NO served route (remove from the "+
			"spec or implement them):\n  %s", strings.Join(unimplemented, "\n  "))
	}
}
