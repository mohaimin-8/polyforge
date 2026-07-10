package controllers

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
	"polyforge/internal/operator/fairness"
	"polyforge/internal/operator/planner"
)

type stubPlanner struct {
	response planner.Response
	err      error
	requests []planner.Request
}

func (s *stubPlanner) Plan(_ context.Context, req planner.Request) (planner.Response, error) {
	s.requests = append(s.requests, req)
	return s.response, s.err
}

type stubDemand struct{ demands map[string]planner.Demand }

func (s stubDemand) TenantDemand(_ context.Context, tid string) (planner.Demand, bool, error) {
	d, ok := s.demands[tid]
	return d, ok, nil
}

type recordingAudit struct{ entries []AuditEntry }

func (r *recordingAudit) Publish(_ context.Context, subject, msgID string, data []byte) error {
	var entry AuditEntry
	if err := json.Unmarshal(data, &entry); err != nil {
		return err
	}
	r.entries = append(r.entries, entry)
	return nil
}

func plannerFixtures(t *testing.T) (client.WithWatch, *pfv1alpha1.Policy) {
	t.Helper()
	tenant := &pfv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{Name: "acme", UID: "uid-acme"},
		Spec:       pfv1alpha1.TenantSpec{IsolationMode: pfv1alpha1.IsolationPool, SLOClass: pfv1alpha1.SLOPremium},
	}
	policy := &pfv1alpha1.Policy{
		ObjectMeta: metav1.ObjectMeta{Name: "acme"},
		Spec: pfv1alpha1.PolicySpec{
			TenantRef:   "acme",
			Replicas:    2,
			ReplicaMin:  1,
			ReplicaMax:  5,
			CacheSizeMB: 128,
			ModelTier:   pfv1alpha1.ModelTierSmall,
		},
	}
	budget := &pfv1alpha1.Budget{
		ObjectMeta: metav1.ObjectMeta{Name: "acme"},
		Spec:       pfv1alpha1.BudgetSpec{TenantRef: "acme", HourlyCapMilliUSD: 2500, FairnessWeightPermille: 700},
	}
	return newTenantClient(t, tenant, policy, budget), policy
}

func demandFor(tid string) stubDemand {
	return stubDemand{demands: map[string]planner.Demand{
		tid: {RPS: map[string]float64{"chat": 2.0}, CrudBaseMs: 50},
	}}
}

func TestPlanRunnerAppliesJointPlan(t *testing.T) {
	c, _ := plannerFixtures(t)
	stub := &stubPlanner{response: planner.Response{
		Solver: "jcac-lattice-v1",
		Plans:  map[string]planner.Plan{"acme": {Replicas: 4, CacheMB: 256, Tier: "mid", ProjectedCostUSD: 0.002}},
	}}
	audit := &recordingAudit{}
	runner := &PlanRunner{Client: c, Planner: stub, Demands: demandFor("acme"), Audit: audit}

	if err := runner.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}

	var got pfv1alpha1.Policy
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Spec.Replicas != 4 || got.Spec.CacheSizeMB != 256 || got.Spec.ModelTier != pfv1alpha1.ModelTierMid {
		t.Errorf("spec = %+v", got.Spec)
	}
	if got.Status.LastPlanSource != pfv1alpha1.PlanSourcePlanner {
		t.Errorf("plan source = %q", got.Status.LastPlanSource)
	}
	if got.Status.LastPlanTime == nil {
		t.Error("lastPlanTime not set")
	}

	// Budget and SLO class flow into the planner request.
	req := stub.requests[0]
	if req.Tenants[0].HourlyBudgetUSD != 2.5 {
		t.Errorf("budget = %v, want 2.5", req.Tenants[0].HourlyBudgetUSD)
	}
	if req.Tenants[0].FairnessWeight != 0.7 {
		t.Errorf("fairness weight = %v, want 0.7", req.Tenants[0].FairnessWeight)
	}
	if req.Tenants[0].SLOClass != "premium" {
		t.Errorf("slo class = %q", req.Tenants[0].SLOClass)
	}

	if len(audit.entries) != 1 {
		t.Fatalf("audit entries = %d, want 1", len(audit.entries))
	}
	entry := audit.entries[0]
	if entry.Source != "planner" || entry.To.Replicas != 4 || entry.From.Replicas != 2 {
		t.Errorf("audit entry = %+v", entry)
	}
}

func TestPlanRunnerClampsPlanToPolicyBounds(t *testing.T) {
	c, _ := plannerFixtures(t)
	stub := &stubPlanner{response: planner.Response{
		Plans: map[string]planner.Plan{"acme": {Replicas: 50, CacheMB: 128, Tier: "small"}},
	}}
	runner := &PlanRunner{Client: c, Planner: stub, Demands: demandFor("acme")}

	if err := runner.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	var got pfv1alpha1.Policy
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	if got.Spec.Replicas != 5 {
		t.Errorf("replicas = %d, want clamped to replicaMax 5", got.Spec.Replicas)
	}
}

func TestPlanRunnerFallsBackOnPlannerFailure(t *testing.T) {
	c, _ := plannerFixtures(t)
	stub := &stubPlanner{err: errors.New("solver exploded")}
	audit := &recordingAudit{}
	runner := &PlanRunner{Client: c, Planner: stub, Demands: demandFor("acme"), Audit: audit}

	if err := runner.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	var got pfv1alpha1.Policy
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme"}, &got); err != nil {
		t.Fatal(err)
	}
	// Last good plan untouched, degradation visible in status + audit.
	if got.Spec.Replicas != 2 || got.Spec.CacheSizeMB != 128 {
		t.Errorf("fallback must not mutate spec, got %+v", got.Spec)
	}
	if got.Status.LastPlanSource != pfv1alpha1.PlanSourceFallback {
		t.Errorf("plan source = %q, want fallback", got.Status.LastPlanSource)
	}
	if len(audit.entries) != 1 || audit.entries[0].Error == "" {
		t.Errorf("audit = %+v", audit.entries)
	}

	// A second failing cycle must not rewrite status (no write storms).
	if err := runner.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(audit.entries) != 1 {
		t.Errorf("repeat fallback re-audited: %d entries", len(audit.entries))
	}
}

func TestPlanRunnerSkipsTenantsWithoutDemand(t *testing.T) {
	c, _ := plannerFixtures(t)
	stub := &stubPlanner{}
	runner := &PlanRunner{Client: c, Planner: stub, Demands: stubDemand{demands: map[string]planner.Demand{}}}

	if err := runner.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(stub.requests) != 0 {
		t.Errorf("planner called with no demand: %+v", stub.requests)
	}
}

type stubInterference struct{ scores map[string]float64 }

func (s stubInterference) TenantInterference(_ context.Context, tid string) float64 {
	return s.scores[tid]
}

// TestNoisyNeighborClosedLoop chains the real W32 detector into the plan
// loop: three windows of eBPF-shaped samples flag the tenant, the next
// plan request carries its interference score, and the fairness gauge
// reflects the planner's projected satisfaction spread. (The throttle
// itself is the planner's move — proven on the Python side by
// test_interference_throttles_noisy_tenant.)
func TestNoisyNeighborClosedLoop(t *testing.T) {
	c, _ := plannerFixtures(t)
	detector := fairness.NewDetector(fairness.DetectorConfig{})
	for range 3 { // 3 windows x 10s control interval = flagged within 30s
		detector.Observe([]fairness.Sample{
			{TenantID: "acme", CPUStallShare: 0.6, SyscallRate: 50_000, MemPressureEvents: 12},
			{TenantID: "quiet-a", CPUStallShare: 0.05, SyscallRate: 2_000},
			{TenantID: "quiet-b", CPUStallShare: 0.06, SyscallRate: 2_500},
		})
	}
	stub := &stubPlanner{response: planner.Response{
		Plans: map[string]planner.Plan{"acme": {Replicas: 1, CacheMB: 64, Tier: "small", ProjectedViolation: 0.2}},
	}}
	runner := &PlanRunner{Client: c, Planner: stub, Demands: demandFor("acme"), Interference: detector}

	if err := runner.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := stub.requests[0].Tenants[0].Interference; got <= 0 {
		t.Errorf("flagged tenant's interference not forwarded, got %v", got)
	}
	if got := testutil.ToFloat64(jainGauge); got != 1.0 { // single tenant: vacuously fair
		t.Errorf("jain gauge = %v, want 1.0", got)
	}
}

func TestPlanRunnerForwardsInterference(t *testing.T) {
	c, _ := plannerFixtures(t)
	stub := &stubPlanner{response: planner.Response{
		Plans: map[string]planner.Plan{"acme": {Replicas: 2, CacheMB: 128, Tier: "small"}},
	}}
	runner := &PlanRunner{
		Client: c, Planner: stub, Demands: demandFor("acme"),
		Interference: stubInterference{scores: map[string]float64{"acme": 0.8}},
	}
	if err := runner.RunOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := stub.requests[0].Tenants[0].Interference; got != 0.8 {
		t.Errorf("interference = %v, want 0.8", got)
	}
}
