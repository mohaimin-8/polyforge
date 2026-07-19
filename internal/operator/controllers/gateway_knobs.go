package controllers

// The gateway knob push (PREREG_WAVE4_LIVE_PLANE.md §Substrate 2-3): the
// Policy's cache/tier levers must reach the live data plane, not only the
// per-tenant ConfigMap. The ConfigMap stays the durable record; this client
// is the immediate actuation path. A push failure keeps the Policy's
// Applied condition false, so the harness's `kubectl wait
// --for=condition=Applied` actuation gate covers all three knobs, not just
// replicas.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// GatewayKnobsPusher actuates a tenant's cache/tier knobs on the data plane.
type GatewayKnobsPusher interface {
	Push(ctx context.Context, tenantID, modelTier string, cacheSizeMB int32) error
}

// HTTPGatewayKnobs pushes knobs to the AI gateway's admin endpoint.
type HTTPGatewayKnobs struct {
	BaseURL  string
	AdminKey string
	Client   *http.Client
}

func NewHTTPGatewayKnobs(baseURL, adminKey string) *HTTPGatewayKnobs {
	return &HTTPGatewayKnobs{
		BaseURL:  baseURL,
		AdminKey: adminKey,
		Client:   &http.Client{Timeout: 5 * time.Second},
	}
}

func (g *HTTPGatewayKnobs) Push(ctx context.Context, tenantID, modelTier string, cacheSizeMB int32) error {
	payload, err := json.Marshal(map[string]any{
		"model_tier":    modelTier,
		"cache_size_mb": cacheSizeMB,
	})
	if err != nil {
		return err
	}
	url := fmt.Sprintf("%s/admin/tenants/%s/knobs", g.BaseURL, tenantID)
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, url, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-PolyForge-Admin-Key", g.AdminKey)
	resp, err := g.Client.Do(req)
	if err != nil {
		return fmt.Errorf("push knobs for %s: %w", tenantID, err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("push knobs for %s: gateway returned %d: %s", tenantID, resp.StatusCode, body)
	}
	return nil
}
