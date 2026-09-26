// Package apicontract reads the "METHOD /path" operations an OpenAPI
// document declares, for the drift gates that hold each service's served
// routes and its contract identical (internal/platform and
// internal/ai/gateway server_openapi_test.go). A small structural scan, not a
// full YAML parse, keeps the gates dependency-free: paths live under a
// top-level "paths:" map as "  /x:" keys, each followed by method keys
// ("    get:", "    post:", ...).
package apicontract

import (
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
)

var (
	pathRE   = regexp.MustCompile(`^ {2}(/\S*):\s*$`)
	methodRE = regexp.MustCompile(`^ {4}(get|post|put|patch|delete):\s*$`)
)

// Operations returns the operations declared in the OpenAPI file at path. It
// fails when the file declares none, since that means the scanner or the
// document changed shape, not that the service has no API.
func Operations(path string) ([]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var ops []string
	inPaths := false
	curPath := ""
	for _, line := range strings.Split(strings.ReplaceAll(string(data), "\r\n", "\n"), "\n") {
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
	if len(ops) == 0 {
		return nil, fmt.Errorf("%s declares no operations: the scanner or the document changed shape", path)
	}
	return ops, nil
}

// Diff compares served route patterns with declared operations and returns
// the served-but-undocumented and documented-but-unserved ones, each sorted.
func Diff(served, declared []string) (undocumented, unserved []string) {
	spec := map[string]bool{}
	for _, op := range declared {
		spec[op] = true
	}
	routes := map[string]bool{}
	for _, p := range served {
		routes[p] = true
		if !spec[p] {
			undocumented = append(undocumented, p)
		}
	}
	for op := range spec {
		if !routes[op] {
			unserved = append(unserved, op)
		}
	}
	sort.Strings(undocumented)
	sort.Strings(unserved)
	return undocumented, unserved
}
