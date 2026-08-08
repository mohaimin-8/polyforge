package platform

// POST /v1/tenants/{tenant_id}/workloads/replay — the eval harness's data
// plane (eval/README.md integration point 3). Each call is one replayed
// request of a declared kind: the handler burns CPU proportional to the
// kind's work units, so the real HPA (CPU metric) and the real scheduler
// see genuine load, and records a telemetry event so `eval-export` can
// compute the run's metrics from the same store the sim's estimators
// mirror. Latency is measured wall time — under saturation it includes
// scheduler queueing, which is exactly what the SLO metrics are about.

import (
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"net/http"
	"strings"
	"time"

	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// Mirrors WORK_UNITS in research/jcac_sim/model.py — keep in lockstep; the
// live replay and the sim must agree on what one request of a kind costs.
var replayWorkUnits = map[string]float64{
	"crud_read":  1.0,
	"crud_write": 2.0,
	"batch":      8.0,
	"embed":      5.0,
	"chat":       20.0,
	"agent":      40.0,
}

// AI kinds carry a model tier so eval-export classifies them into the AI
// latency family (mirrors AI_KINDS in model.py).
var replayAIKinds = map[string]bool{"chat": true, "embed": true, "agent": true}

// newReplayRateTracker builds the per-(tenant, kind) rate tracker that fills
// Event.RPSWindow. It lives here rather than inline in NewServer because that
// constructor takes a parameter named `telemetry`, which shadows the package.
func newReplayRateTracker() *telemetry.RateTracker {
	return telemetry.NewRateTracker(telemetry.RateWindow)
}

// One work unit costs this much CPU. REPLICA_CAPACITY_WU=100 wu/s in the
// sim means one replica saturates at 100 wu/s; at 1 ms of CPU per wu, one
// CPU core is exactly one sim replica — the scale the eval chart's CPU
// requests are stated against.
const replayCPUPerWU = time.Millisecond

type replayRequest struct {
	Kind string `json:"kind"`
	Run  string `json:"run"`
}

func (s *Server) replayWorkload(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	var req replayRequest
	if err := readJSON(w, r, &req); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	req.Kind = strings.TrimSpace(req.Kind)
	wu, ok := replayWorkUnits[req.Kind]
	if !ok {
		s.writeError(w, r, http.StatusBadRequest, errors.New("kind must be one of the workload taxonomy kinds"))
		return
	}

	started := time.Now()
	burnCPU(time.Duration(wu * float64(replayCPUPerWU)))
	latencyMS := float64(time.Since(started)) / float64(time.Millisecond)

	tier := ""
	if replayAIKinds[req.Kind] {
		tier = "small"
	}
	// RPSWindow is the planner's entire demand signal: operator/planner
	// demand.go sums this field per kind, so leaving it zero — as this
	// handler did — reports an idle tenant under any load, and the joint
	// controller then holds its initial configuration for the whole run.
	event := telemetry.Event{
		TenantID:  tenantID,
		Service:   req.Kind,
		Timestamp: time.Now().UTC(),
		RPSWindow: s.rates.Observe(tenantID + "|" + req.Kind),
		LatencyMS: latencyMS,
		ModelTier: tier,
	}
	if _, err := s.telemetry.Add(r.Context(), event); err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	s.metrics.RecordTelemetryEvent(event)
	writeJSON(w, http.StatusAccepted, map[string]any{
		"kind": req.Kind, "run": req.Run, "latency_ms": latencyMS,
	})
}

// burnCPU spins this goroutine for the given CPU duration. Hashing keeps
// the loop opaque to the compiler; the time check is amortized so the
// clock read does not dominate the work being metered.
func burnCPU(d time.Duration) {
	deadline := time.Now().Add(d)
	var buf [32]byte
	binary.LittleEndian.PutUint64(buf[:8], uint64(d))
	for {
		for i := 0; i < 64; i++ {
			buf = sha256.Sum256(buf[:])
		}
		if !time.Now().Before(deadline) {
			return
		}
	}
}
