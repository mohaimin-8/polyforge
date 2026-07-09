// Package online runs the W27 classification loop: every interval it
// pulls each tenant's recent telemetry, engineers the W26 feature vector,
// predicts the workload class, records it with history, publishes label
// *changes* to the event backbone, and scores population drift with PSI.
//
// Deliberate deviation from the roadmap sketch: this runs as a goroutine
// inside the control plane rather than a separate /services/classifier
// microservice. The loop is a poll-predict-record cycle with no state of
// its own beyond the registry; a second binary would add a deploy unit and
// an RPC hop purely for org-chart reasons. The package boundary keeps the
// extraction trivial if the M8 operator wants it separate.
package online

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"polyforge/internal/classifier"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// Predictor is the model seam: the trained W26 artifact in production,
// the W19 rules through RulePredictor when no artifact is configured.
type Predictor interface {
	Predict(features [classifier.FeatureCount]float64) (classifier.Label, float64)
}

// RulePredictor adapts the W19 rule classifier to the Predictor seam; it
// re-derives a Result from raw events, so the poller hands it those too.
type RulePredictor struct{}

func (RulePredictor) Predict([classifier.FeatureCount]float64) (classifier.Label, float64) {
	return classifier.Unknown, 0
}

// Publisher matches events.Backbone.Publish.
type Publisher interface {
	Publish(ctx context.Context, subject, msgID string, data []byte) error
}

// Sample is one classification observation.
type Sample struct {
	At         time.Time        `json:"at"`
	Label      classifier.Label `json:"label"`
	Confidence float64          `json:"confidence"`
}

// TenantWorkload is the current state served to the admin UI.
type TenantWorkload struct {
	TenantID    string           `json:"tenant_id"`
	Label       classifier.Label `json:"label"`
	Confidence  float64          `json:"confidence"`
	Since       time.Time        `json:"since"`
	ObservedAt  time.Time        `json:"observed_at"`
	EventsInWin int              `json:"events_in_window"`
}

// DriftStatus is the population-level PSI state.
type DriftStatus struct {
	Drifted      bool      `json:"drifted"`
	WorstFeature string    `json:"worst_feature"`
	WorstPSI     float64   `json:"worst_psi"`
	WindowSize   int       `json:"window_size"`
	CheckedAt    time.Time `json:"checked_at"`
}

// Registry holds current labels and bounded per-tenant history. It is the
// in-process form of the roadmap's tenant_workload_class table; the
// durable form is a ClickHouse table fed by the same records (schema in
// deploy/clickhouse/schema.sql once a cluster exists to apply it to).
type Registry struct {
	mu         sync.RWMutex
	current    map[string]TenantWorkload
	history    map[string][]Sample
	drift      DriftStatus
	historyCap int
}

func NewRegistry(historyCap int) *Registry {
	if historyCap <= 0 {
		historyCap = 8640 // 24h at one sample per 10s
	}
	return &Registry{
		current:    map[string]TenantWorkload{},
		history:    map[string][]Sample{},
		historyCap: historyCap,
	}
}

// record stores an observation and reports whether the label changed.
func (r *Registry) record(tenantID string, label classifier.Label, confidence float64, eventCount int, at time.Time) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	previous, existed := r.current[tenantID]
	changed := !existed || previous.Label != label
	state := TenantWorkload{
		TenantID:    tenantID,
		Label:       label,
		Confidence:  confidence,
		Since:       previous.Since,
		ObservedAt:  at,
		EventsInWin: eventCount,
	}
	if changed {
		state.Since = at
	}
	r.current[tenantID] = state
	samples := append(r.history[tenantID], Sample{At: at, Label: label, Confidence: confidence})
	if len(samples) > r.historyCap {
		samples = samples[len(samples)-r.historyCap:]
	}
	r.history[tenantID] = samples
	return changed
}

func (r *Registry) setDrift(status DriftStatus) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.drift = status
}

// Snapshot returns current labels for every observed tenant plus drift.
func (r *Registry) Snapshot() ([]TenantWorkload, DriftStatus) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]TenantWorkload, 0, len(r.current))
	for _, w := range r.current {
		out = append(out, w)
	}
	return out, r.drift
}

// History returns the bounded label history for one tenant.
func (r *Registry) History(tenantID string) []Sample {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return append([]Sample(nil), r.history[tenantID]...)
}

// Config wires a Poller.
type Config struct {
	Interval    time.Duration // default 10s
	WindowLimit int           // events pulled per tenant, default 500
	// DriftWindow is how many recent feature vectors feed each PSI check;
	// default 200.
	DriftWindow int
	Now         func() time.Time
}

// Poller drives the loop.
type Poller struct {
	tenants   tenant.Repository
	telemetry telemetry.Repository
	predictor Predictor
	detector  *classifier.DriftDetector
	publisher Publisher
	registry  *Registry
	log       *slog.Logger
	cfg       Config

	recentMu sync.Mutex
	recent   [][classifier.FeatureCount]float64
}

func NewPoller(
	tenants tenant.Repository,
	telemetryRepo telemetry.Repository,
	predictor Predictor,
	detector *classifier.DriftDetector,
	publisher Publisher,
	registry *Registry,
	log *slog.Logger,
	cfg Config,
) *Poller {
	if cfg.Interval <= 0 {
		cfg.Interval = 10 * time.Second
	}
	if cfg.WindowLimit <= 0 {
		cfg.WindowLimit = 500
	}
	if cfg.DriftWindow <= 0 {
		cfg.DriftWindow = 200
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	return &Poller{
		tenants:   tenants,
		telemetry: telemetryRepo,
		predictor: predictor,
		detector:  detector,
		publisher: publisher,
		registry:  registry,
		log:       log,
		cfg:       cfg,
	}
}

// Run loops Tick until the context ends.
func (p *Poller) Run(ctx context.Context) {
	ticker := time.NewTicker(p.cfg.Interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			p.Tick(ctx)
		}
	}
}

// Tick classifies every tenant once. Errors are logged and skipped — a
// failing tenant must not starve the rest of the loop.
func (p *Poller) Tick(ctx context.Context) {
	tenants, err := p.tenants.ListTenants(ctx)
	if err != nil {
		p.log.Warn("classifier poll: list tenants", "error", err)
		return
	}
	now := p.cfg.Now()
	for _, t := range tenants {
		events, err := p.telemetry.RecentByTenant(ctx, t.ID, p.cfg.WindowLimit)
		if err != nil {
			p.log.Warn("classifier poll: telemetry", "tenant", t.ID, "error", err)
			continue
		}
		if len(events) == 0 {
			continue
		}
		features := classifier.Features(events)
		label, confidence := p.predictor.Predict(features)
		if label == classifier.Unknown {
			// Rules fallback path: predict from raw events.
			result := classifier.Classify(t.ID, events)
			label, confidence = result.Label, result.Confidence
		}
		changed := p.registry.record(t.ID, label, confidence, len(events), now)
		if changed && p.publisher != nil {
			p.publishChange(ctx, t.ID, label, confidence, now)
		}
		p.trackDrift(features, now)
	}
}

func (p *Poller) publishChange(ctx context.Context, tenantID string, label classifier.Label, confidence float64, at time.Time) {
	payload, err := json.Marshal(map[string]any{
		"tenant_id":  tenantID,
		"label":      label,
		"confidence": confidence,
		"at":         at.UTC(),
	})
	if err != nil {
		p.log.Warn("classifier poll: encode label change", "tenant", tenantID, "error", err)
		return
	}
	subject := "polyforge.workload." + tenantID
	msgID := fmt.Sprintf("workload-%s-%d", tenantID, at.UnixNano())
	if err := p.publisher.Publish(ctx, subject, msgID, payload); err != nil {
		p.log.Warn("classifier poll: publish label change", "tenant", tenantID, "error", err)
	}
}

func (p *Poller) trackDrift(features [classifier.FeatureCount]float64, now time.Time) {
	if p.detector == nil {
		return
	}
	p.recentMu.Lock()
	p.recent = append(p.recent, features)
	if len(p.recent) > p.cfg.DriftWindow {
		p.recent = p.recent[len(p.recent)-p.cfg.DriftWindow:]
	}
	window := append([][classifier.FeatureCount]float64(nil), p.recent...)
	p.recentMu.Unlock()
	// PSI over a thin window is noise, not signal.
	if len(window) < p.cfg.DriftWindow/2 {
		return
	}
	drifted, feature, psi := p.detector.Drifted(window)
	status := DriftStatus{
		Drifted:      drifted,
		WorstFeature: feature,
		WorstPSI:     psi,
		WindowSize:   len(window),
		CheckedAt:    now,
	}
	p.registry.setDrift(status)
	if drifted {
		p.log.Warn("workload feature drift detected — retrain due",
			"feature", feature, "psi", psi, "window", len(window))
	}
}
