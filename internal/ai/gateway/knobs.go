package gateway

// Per-tenant data-plane knobs (PREREG_WAVE4_LIVE_PLANE.md §Substrate 2-3).
// The operator's Policy carries three levers; replicas actuate on the
// Deployment, and these two actuate here: the model tier a cache miss is
// served from and the semantic-cache byte budget. The admin endpoint is the
// push target for the operator's PolicyReconciler, so a Policy edit reaches
// the live data plane within one reconcile rather than waiting on a
// ConfigMap volume sync. Knobs are process state: after a gateway restart
// they are re-pushed by the next reconcile of each Policy.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
)

// TenantKnobs is the wire shape of PUT/GET /admin/tenants/{id}/knobs.
type TenantKnobs struct {
	// ModelTier pins the tenant's cache misses to one tier backend
	// (small/mid/large). Empty or "none" releases the pin and the request
	// falls back to the router / default provider.
	ModelTier string `json:"model_tier"`
	// CacheSizeMB is the tenant's semantic-cache budget. 0 disables the
	// tenant's cache entirely; a negative value is rejected.
	CacheSizeMB int `json:"cache_size_mb"`
}

type knobStore struct {
	mu    sync.RWMutex
	knobs map[string]TenantKnobs
}

func newKnobStore() *knobStore {
	return &knobStore{knobs: map[string]TenantKnobs{}}
}

func (k *knobStore) get(tenantID string) (TenantKnobs, bool) {
	k.mu.RLock()
	defer k.mu.RUnlock()
	v, ok := k.knobs[tenantID]
	return v, ok
}

func (k *knobStore) set(tenantID string, v TenantKnobs) {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.knobs[tenantID] = v
}

// knobTier is the tenant's pinned tier, "" when unpinned. "none" is the
// sim's no-tier value; it means unpinned here for the same reason.
func (s *Server) knobTier(tenantID string) string {
	v, ok := s.knobs.get(tenantID)
	if !ok {
		return ""
	}
	tier := strings.TrimSpace(v.ModelTier)
	if tier == "none" {
		return ""
	}
	return tier
}

// authorizeAdmin gates the knob endpoints exactly like the control plane's
// admin surface: a static key in X-PolyForge-Admin-Key. With no admin key
// configured the surface does not exist (404-equivalent 501), so a
// production gateway that never sets POLYFORGE_ADMIN_KEY exposes nothing.
func (s *Server) authorizeAdmin(w http.ResponseWriter, r *http.Request) bool {
	if s.adminKey == "" {
		writeError(w, http.StatusNotImplemented, "not_configured",
			"no admin key is configured; the knob surface is disabled")
		return false
	}
	if strings.TrimSpace(r.Header.Get("X-PolyForge-Admin-Key")) != s.adminKey {
		writeError(w, http.StatusUnauthorized, "unauthorized", "a valid admin key is required")
		return false
	}
	return true
}

func (s *Server) putKnobs(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeAdmin(w, r) {
		return
	}
	tenantID := r.PathValue("tenant_id")
	var input TenantKnobs
	if err := readJSON(w, r, &input); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if input.CacheSizeMB < 0 {
		writeError(w, http.StatusBadRequest, "invalid_request", "cache_size_mb must be >= 0")
		return
	}
	tier := strings.TrimSpace(input.ModelTier)
	if tier != "" && tier != "none" {
		if _, ok := s.tierProviders[tier]; !ok {
			// An unknown tier must fail loudly: silently falling back to the
			// default provider would be exactly the inert-knob failure the
			// WL-H2 preflight gate exists to catch.
			writeError(w, http.StatusBadRequest, "invalid_request", fmt.Sprintf(
				"model_tier %q has no configured backend (have: %s)",
				tier, strings.Join(s.tierNames(), ", ")))
			return
		}
	}
	s.knobs.set(tenantID, TenantKnobs{ModelTier: tier, CacheSizeMB: input.CacheSizeMB})
	s.cache.SetTenantBudgetBytes(tenantID, int64(input.CacheSizeMB)*1024*1024)
	writeJSON(w, http.StatusOK, map[string]any{
		"tenant_id":     tenantID,
		"model_tier":    tier,
		"cache_size_mb": input.CacheSizeMB,
	})
}

func (s *Server) getKnobs(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeAdmin(w, r) {
		return
	}
	tenantID := r.PathValue("tenant_id")
	v, ok := s.knobs.get(tenantID)
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "no knobs are set for this tenant")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"tenant_id":        tenantID,
		"model_tier":       v.ModelTier,
		"cache_size_mb":    v.CacheSizeMB,
		"cache_used_bytes": s.cache.UsedBytes(tenantID),
		"cache_budget_set": true,
	})
}

func (s *Server) tierNames() []string {
	names := make([]string, 0, len(s.tierProviders))
	for name := range s.tierProviders {
		names = append(names, name)
	}
	return names
}

// TierBackendSpec is one entry of the POLYFORGE_TIER_BACKENDS JSON map:
// tier name -> how to reach the model that serves it.
type TierBackendSpec struct {
	// Kind selects the adapter: "openai" (any OpenAI-compatible server:
	// vLLM, llama.cpp, Ollama's /v1) or "ollama" (native API).
	Kind    string `json:"kind"`
	BaseURL string `json:"base_url"`
	Model   string `json:"model"`
	// APIKey is optional; local model servers need none.
	APIKey string `json:"api_key,omitempty"`
}

// BuildTierProviders turns the POLYFORGE_TIER_BACKENDS JSON into the
// tier -> Provider map the server routes on. An empty input is a valid
// no-tier deployment.
func BuildTierProviders(raw string) (map[string]Provider, error) {
	if strings.TrimSpace(raw) == "" {
		return nil, nil
	}
	var specs map[string]TierBackendSpec
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&specs); err != nil {
		return nil, fmt.Errorf("parse tier backends: %w", err)
	}
	providers := make(map[string]Provider, len(specs))
	for tier, spec := range specs {
		if spec.BaseURL == "" || spec.Model == "" {
			return nil, fmt.Errorf("tier %q needs base_url and model", tier)
		}
		switch spec.Kind {
		case "", "openai":
			providers[tier] = NewOpenAICompat(spec.BaseURL, spec.APIKey, spec.Model)
		case "ollama":
			providers[tier] = NewOllama(spec.BaseURL, spec.Model)
		default:
			return nil, fmt.Errorf("tier %q has unknown kind %q (openai|ollama)", tier, spec.Kind)
		}
	}
	return providers, nil
}
