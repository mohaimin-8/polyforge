package controllers

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	rbacv1 "k8s.io/api/rbac/v1"
	"sigs.k8s.io/yaml"
)

// The two shipped copies of the operator's RBAC. They are meant to mirror each
// other; the test checks both so a fix applied to one does not leave the other
// broken, which is how the omission survived in the first place.
var operatorRBACManifests = []string{
	"../../../deploy/helm/polyforge-operator/templates/rbac.yaml",
	"../../../deploy/operator/rbac.yaml",
}

// clusterRoleRules pulls the ClusterRole's rules out of a manifest that may be
// a Helm template. Templating appears only in metadata (names and labels), never
// inside `rules:`, so dropping lines containing an action delimiter leaves valid
// YAML for the part under test. A manifest whose rules ever become templated
// will fail to parse here rather than silently returning nothing.
func clusterRoleRules(t *testing.T, path string) []rbacv1.PolicyRule {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", filepath.Base(path), err)
	}
	// Decode each document and select on the typed Kind. Matching the string
	// "kind: ClusterRole" instead would also match the indented roleRef block
	// inside a ClusterRoleBinding, which names the same kind and carries no
	// rules at all.
	for _, doc := range strings.Split(string(raw), "\n---") {
		var kept []string
		for _, line := range strings.Split(doc, "\n") {
			if !strings.Contains(line, "{{") {
				kept = append(kept, line)
			}
		}
		var cr rbacv1.ClusterRole
		if err := yaml.Unmarshal([]byte(strings.Join(kept, "\n")), &cr); err != nil {
			continue // a document of some other shape, not this test's business
		}
		if cr.Kind == "ClusterRole" && len(cr.Rules) > 0 {
			return cr.Rules
		}
	}
	t.Fatalf("no ClusterRole with rules found in %s", filepath.Base(path))
	return nil
}

func held(rules []rbacv1.PolicyRule, group, resource, verb string) bool {
	in := func(hay []string, needle string) bool {
		for _, h := range hay {
			if h == needle || h == rbacv1.ResourceAll || h == rbacv1.VerbAll {
				return true
			}
		}
		return false
	}
	for _, r := range rules {
		if in(r.APIGroups, group) && in(r.Resources, resource) && in(r.Verbs, verb) {
			return true
		}
	}
	return false
}

// TestOperatorClusterRoleCoversTenantRoleRules pins the invariant the shipped
// chart violated: Kubernetes refuses to let a subject create a Role granting
// permissions the subject does not itself hold. The operator creates a
// per-tenant Role from TenantRoleRules, so its ClusterRole must cover every
// group/resource/verb in there.
//
// Before the fix this failed on pods for both manifests. In the WP14 soak the
// apiserver rejected the Role 552 times in 14 h with "attempting to grant RBAC
// permissions not currently held", so no tenant ever received one -- and
// nothing surfaced it, because ensureRBAC's error is logged while
// reconciliation carries on and every pod stays Running.
//
// Asserting against TenantRoleRules rather than a literal "pods" is the point:
// adding a resource to the tenant Role without granting it to the operator
// fails here instead of silently in a cluster.
func TestOperatorClusterRoleCoversTenantRoleRules(t *testing.T) {
	for _, manifest := range operatorRBACManifests {
		manifest := manifest
		t.Run(filepath.Base(filepath.Dir(manifest)), func(t *testing.T) {
			granted := clusterRoleRules(t, manifest)
			for _, rule := range TenantRoleRules() {
				for _, group := range rule.APIGroups {
					for _, resource := range rule.Resources {
						for _, verb := range rule.Verbs {
							if !held(granted, group, resource, verb) {
								t.Errorf(
									"operator ClusterRole in %s does not hold %q/%q verb %q, "+
										"but per-tenant Roles delegate it: the apiserver will "+
										"reject every tenant Role with \"attempting to grant RBAC "+
										"permissions not currently held\"",
									manifest, group, resource, verb)
							}
						}
					}
				}
			}
		})
	}
}
