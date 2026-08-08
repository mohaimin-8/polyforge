package replay

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"polyforge/internal/telemetry"
)

// kindProfile translates a trace request kind into the telemetry shape the
// platform emits for that workload.
type kindProfile struct {
	service          string
	modelTier        string
	childSpans       int
	embeddingDensity float64
}

var kindProfiles = map[string]kindProfile{
	"crud_read":  {service: "projects-api"},
	"crud_write": {service: "projects-api"},
	"batch":      {service: "worker"},
	// Tiers must be from the priced taxonomy {none,small,mid,large}; the
	// control plane rejects anything else at ingest and eval-export prices
	// only these. The earlier "local"/"quality" labels were outside it, so
	// every replayed AI request was silently booked at $0. small = the cheap
	// serving tier, large = the quality tier, mirroring model.py's TIERS.
	"chat":  {service: "ai-gateway", modelTier: "small", embeddingDensity: 0.6},
	"embed": {service: "ai-gateway", modelTier: "small", embeddingDensity: 0.9},
	"agent": {service: "ai-gateway", modelTier: "large", childSpans: 4, embeddingDensity: 0.3},
}

// HTTPSender posts replayed events to the control plane's telemetry ingest
// using each target tenant's API key.
type HTTPSender struct {
	BaseURL string
	Client  *http.Client
}

func (s *HTTPSender) Send(ctx context.Context, event ScheduledEvent) error {
	profile := kindProfiles[event.RequestKind]
	body, err := json.Marshal(telemetry.Event{
		TenantID:         event.Target.TenantID,
		Service:          profile.service,
		Timestamp:        time.UnixMilli(event.TimestampMS).UTC(),
		RPSWindow:        event.SourceRPS,
		PayloadBytes:     int(event.PayloadBytes),
		LatencyMS:        event.ExpectedLatencyMS,
		EmbeddingDensity: profile.embeddingDensity,
		ModelTier:        profile.modelTier,
		ChildSpans:       profile.childSpans,
	})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.BaseURL+"/v1/telemetry", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-PolyForge-API-Key", event.Target.APIKey)
	client := s.Client
	if client == nil {
		client = http.DefaultClient
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusAccepted {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 256))
		return fmt.Errorf("replay: ingest returned %d: %s", resp.StatusCode, detail)
	}
	return nil
}
