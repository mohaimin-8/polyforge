//go:build envtest

// Package controllers' envtest suite: the operator against a REAL apiserver.
//
// Build-tagged so `go test ./...` stays green on a machine without the
// kube-apiserver/etcd binaries. Run it with:
//
//	setup-envtest use 1.31.0 --bin-dir ~/.local/share/kubebuilder-envtest
//	KUBEBUILDER_ASSETS=$(setup-envtest use 1.31.0 --bin-dir ~/.local/share/kubebuilder-envtest -p path) \
//	  go test -tags envtest ./internal/operator/controllers/ -run Envtest -v
//
// WHY THIS EXISTS. Every other test in this package uses a fake client, and
// a fake client cannot reproduce the failure modes that the session-35 audit
// confirmed are real:
//
//   - it does not enforce optimistic concurrency, so no test could observe
//     the 409 that dropped an audit record precisely on the cycles where a
//     knob moved (biased low, in the proposal's favour);
//   - it does not run CEL/OpenAPI validation, so an inverted bound band was
//     accepted and the two clamp paths silently disagreed about it;
//   - it does not populate status subresources, so `Applied=True` written
//     from a spec patch looked indistinguishable from actuation.
//
// Reporting `go test -race` as clean on the fake-client suite would be
// misleading in an artifact appendix: PlanRunner and PolicyReconciler are
// never co-scheduled there, so the detector has nothing to observe.
package controllers

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
	"polyforge/internal/operator/planner"
)

func startEnv(t *testing.T) (client.Client, func()) {
	t.Helper()
	if os.Getenv("KUBEBUILDER_ASSETS") == "" {
		t.Skip("KUBEBUILDER_ASSETS unset; run setup-envtest first")
	}
	env := &envtest.Environment{
		CRDDirectoryPaths:     []string{filepath.Join("..", "..", "..", "deploy", "operator", "crds")},
		ErrorIfCRDPathMissing: true,
	}
	cfg, err := env.Start()
	if err != nil {
		t.Fatalf("start envtest: %v", err)
	}
	if err := pfv1alpha1.AddToScheme(scheme.Scheme); err != nil {
		t.Fatalf("add scheme: %v", err)
	}
	c, err := client.New(cfg, client.Options{Scheme: scheme.Scheme})
	if err != nil {
		t.Fatalf("build client: %v", err)
	}
	return c, func() { _ = env.Stop() }
}

var _ = rest.Config{} // keep the import honest across controller-runtime versions

// The CRD must reject an inverted bound band. Fake clients skip validation
// entirely, so this rule was unenforceable until now — and an unnoticed
// inverted band turns a pinned ablation arm into an unlabelled copy of the
// full controller.
func TestEnvtestCRDRejectsInvertedBands(t *testing.T) {
	c, stop := startEnv(t)
	defer stop()
	ctx := context.Background()

	bad := &pfv1alpha1.Policy{
		ObjectMeta: metav1.ObjectMeta{Name: "inverted"},
		Spec: pfv1alpha1.PolicySpec{
			TenantRef: "acme", Replicas: 2, ReplicaMin: 5, ReplicaMax: 1,
		},
	}
	if err := c.Create(ctx, bad); err == nil {
		t.Error("apiserver accepted replicaMin > replicaMax; CEL rule missing")
	}

	badCache := &pfv1alpha1.Policy{
		ObjectMeta: metav1.ObjectMeta{Name: "inverted-cache"},
		Spec: pfv1alpha1.PolicySpec{
			TenantRef: "acme", Replicas: 2, ReplicaMin: 1, ReplicaMax: 5,
			CacheSizeMBMin: 512, CacheSizeMBMax: 128,
		},
	}
	if err := c.Create(ctx, badCache); err == nil {
		t.Error("apiserver accepted cacheSizeMBMin > cacheSizeMBMax")
	}

	good := &pfv1alpha1.Policy{
		ObjectMeta: metav1.ObjectMeta{Name: "valid"},
		Spec: pfv1alpha1.PolicySpec{
			TenantRef: "acme", Replicas: 2, ReplicaMin: 1, ReplicaMax: 5,
			CacheSizeMBMin: 128, CacheSizeMBMax: 128, // a legitimate pin
		},
	}
	if err := c.Create(ctx, good); err != nil {
		t.Errorf("apiserver rejected a valid pinned policy: %v", err)
	}
}

// Every spec change must produce a matching audit record even when the
// status subresource is contended. Against a real apiserver the spec Update
// bumps resourceVersion and the status write can 409 — the exact race that
// used to swallow the record on precisely the cycles where a knob moved.
func TestEnvtestAuditSurvivesStatusConflict(t *testing.T) {
	c, stop := startEnv(t)
	defer stop()
	ctx := context.Background()

	tenant := &pfv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{Name: "acme"},
		Spec:       pfv1alpha1.TenantSpec{IsolationMode: pfv1alpha1.IsolationPool, SLOClass: pfv1alpha1.SLOPremium},
	}
	if err := c.Create(ctx, tenant); err != nil {
		t.Fatal(err)
	}
	policy := &pfv1alpha1.Policy{
		ObjectMeta: metav1.ObjectMeta{Name: "acme"},
		Spec: pfv1alpha1.PolicySpec{
			TenantRef: "acme", Replicas: 2, ReplicaMin: 1, ReplicaMax: 5,
			CacheSizeMB: 128, ModelTier: pfv1alpha1.ModelTierSmall,
		},
	}
	if err := c.Create(ctx, policy); err != nil {
		t.Fatal(err)
	}

	audit := &recordingAudit{}
	stub := &stubPlanner{response: planner.Response{
		Solver: "jcac-lattice-v1",
		Plans:  map[string]planner.Plan{"acme": {Replicas: 4, CacheMB: 256, Tier: "mid"}},
	}}
	runner := &PlanRunner{Client: c, Planner: stub, Demands: demandFor("acme"), Audit: audit}

	// Race a competing status writer against the plan cycle, which is what
	// PolicyReconciler does in production.
	done := make(chan struct{})
	go func() {
		defer close(done)
		for i := 0; i < 20; i++ {
			var p pfv1alpha1.Policy
			if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &p); err == nil {
				p.Status.AppliedReplicas = int32(i)
				_ = c.Status().Update(ctx, &p)
			}
			time.Sleep(2 * time.Millisecond)
		}
	}()

	if err := runner.RunOnce(ctx); err != nil {
		t.Fatalf("RunOnce under status contention: %v", err)
	}
	<-done

	var got pfv1alpha1.Policy
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Spec.Replicas != 4 || got.Spec.CacheSizeMB != 256 {
		t.Errorf("plan not applied under contention: %+v", got.Spec)
	}
	if len(audit.entries) != 1 {
		t.Fatalf("audit entries = %d, want 1 — a spec change must always be "+
			"recorded, contention or not", len(audit.entries))
	}
	if audit.entries[0].To.Replicas != 4 {
		t.Errorf("audit recorded %+v, want the applied plan", audit.entries[0].To)
	}
}

// A pinned knob must stay pinned when the planner returns something else,
// through a real apiserver round-trip including defaulting and validation.
func TestEnvtestPinnedKnobsHoldThroughApiserver(t *testing.T) {
	c, stop := startEnv(t)
	defer stop()
	ctx := context.Background()

	if err := c.Create(ctx, &pfv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{Name: "acme"},
		Spec:       pfv1alpha1.TenantSpec{IsolationMode: pfv1alpha1.IsolationPool, SLOClass: pfv1alpha1.SLOPremium},
	}); err != nil {
		t.Fatal(err)
	}
	if err := c.Create(ctx, &pfv1alpha1.Policy{
		ObjectMeta: metav1.ObjectMeta{Name: "acme"},
		Spec: pfv1alpha1.PolicySpec{
			TenantRef: "acme", Replicas: 2, ReplicaMin: 2, ReplicaMax: 2,
			CacheSizeMB: 128, CacheSizeMBMin: 128, CacheSizeMBMax: 128,
			ModelTier:    pfv1alpha1.ModelTierSmall,
			ModelTierMin: pfv1alpha1.ModelTierSmall, ModelTierMax: pfv1alpha1.ModelTierSmall,
		},
	}); err != nil {
		t.Fatal(err)
	}

	stub := &stubPlanner{response: planner.Response{
		Plans: map[string]planner.Plan{"acme": {Replicas: 5, CacheMB: 1024, Tier: "large"}},
	}}
	runner := &PlanRunner{Client: c, Planner: stub, Demands: demandFor("acme")}
	if err := runner.RunOnce(ctx); err != nil {
		t.Fatal(err)
	}

	var got pfv1alpha1.Policy
	if err := c.Get(ctx, types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Spec.Replicas != 2 || got.Spec.CacheSizeMB != 128 ||
		got.Spec.ModelTier != pfv1alpha1.ModelTierSmall {
		t.Errorf("pins did not hold through the apiserver: %+v", got.Spec)
	}
}
