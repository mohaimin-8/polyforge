package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/vector"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

const maxBodyBytes = 1 << 20

// AgentFunc runs an agent loop for one prompt. The agent package supplies
// it; the gateway stays ignorant of tool-use mechanics.
type AgentFunc func(ctx context.Context, tenantID, prompt string) (any, int, error)

type Server struct {
	log           *slog.Logger
	provider      Provider
	router        *Router
	cache         *SemanticCache
	embedder      embed.Embedder
	search        *vector.Index
	tenants       tenant.Repository
	telemetry     telemetry.Repository
	agent         AgentFunc
	modelTier     string
	tierProviders map[string]Provider
	knobs         *knobStore
	adminKey      string
	mux           *http.ServeMux
	requests      atomic.Int64
	// rates fills Event.RPSWindow, the planner's demand signal. Without it
	// the operator plans every tenant against zero AI demand.
	rates *telemetry.RateTracker
}

type Config struct {
	Provider Provider
	// Router, when set, picks the backend per request (roadmap W20); the
	// chosen Provider then overrides Provider for that request.
	Router   *Router
	Embedder embed.Embedder
	Tenants  tenant.Repository
	// Telemetry, when set, records one event per AI request so the
	// classifier sees the AI workload classes the gateway generates.
	Telemetry telemetry.Repository
	// Agent, when set, enables POST /v1/tenants/{tenant_id}/ai/agent.
	Agent AgentFunc
	// CacheThreshold defaults to DefaultCacheThreshold.
	CacheThreshold float64
	// ModelTier labels this gateway's telemetry (small/mid/large).
	ModelTier string
	// CacheShared enables the INSECURE single-partition cache posture
	// (off by default). Only the wire-attack baseline sets it; a production
	// gateway must not. See SemanticCache.shared.
	CacheShared bool
	// TierProviders, when set, makes the per-tenant model-tier knob a real
	// routing lever: tier name -> backend (PREREG_WAVE4_LIVE_PLANE.md
	// §Substrate 2). Requests from a tenant whose knob pins a tier are
	// served by that tier's provider, and telemetry records the tier
	// actually hit.
	TierProviders map[string]Provider
	// AdminKey guards PUT/GET /admin/tenants/{id}/knobs, the operator's
	// knob push target. Empty disables the admin surface entirely.
	AdminKey string
}

func NewServer(log *slog.Logger, cfg Config) *Server {
	if log == nil {
		log = slog.Default()
	}
	if cfg.ModelTier == "" {
		cfg.ModelTier = "mid"
	}
	s := &Server{
		log:           log,
		provider:      cfg.Provider,
		router:        cfg.Router,
		cache:         NewSemanticCache(cfg.Embedder, cfg.CacheThreshold).WithShared(cfg.CacheShared),
		embedder:      cfg.Embedder,
		search:        vector.NewIndex(cfg.Embedder.Dimensions()),
		tenants:       cfg.Tenants,
		telemetry:     cfg.Telemetry,
		agent:         cfg.Agent,
		modelTier:     cfg.ModelTier,
		tierProviders: cfg.TierProviders,
		knobs:         newKnobStore(),
		adminKey:      cfg.AdminKey,
		mux:           http.NewServeMux(),
		rates:         telemetry.NewRateTracker(telemetry.RateWindow),
	}
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("GET /metrics", s.metrics)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/ai/chat", s.chat)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/ai/index", s.indexDoc)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/ai/search", s.searchDocs)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/ai/agent", s.runAgent)
	s.mux.HandleFunc("PUT /admin/tenants/{tenant_id}/knobs", s.putKnobs)
	s.mux.HandleFunc("GET /admin/tenants/{tenant_id}/knobs", s.getKnobs)
	return s
}

func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "service": "polyforge-ai-gateway"})
}

func (s *Server) metrics(w http.ResponseWriter, _ *http.Request) {
	hits, misses := s.cache.Stats()
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	_, _ = fmt.Fprintf(w, "# TYPE aigateway_requests_total counter\naigateway_requests_total %d\n", s.requests.Load())
	_, _ = fmt.Fprintf(w, "# TYPE aigateway_cache_hits_total counter\naigateway_cache_hits_total %d\n", hits)
	_, _ = fmt.Fprintf(w, "# TYPE aigateway_cache_misses_total counter\naigateway_cache_misses_total %d\n", misses)
}

// authorize authenticates the tenant API key exactly like the control
// plane: 401 for a bad credential, 403 for missing scope.
func (s *Server) authorize(w http.ResponseWriter, r *http.Request, tenantID, requiredScope string) bool {
	secret := strings.TrimSpace(r.Header.Get("X-PolyForge-API-Key"))
	if secret == "" {
		writeError(w, http.StatusUnauthorized, "unauthorized", "an API key is required")
		return false
	}
	key, err := s.tenants.AuthenticateAPIKey(r.Context(), tenantID, secret)
	if err != nil {
		status := http.StatusUnauthorized
		if !errors.Is(err, tenant.ErrUnauthorized) {
			status = http.StatusInternalServerError
		}
		writeError(w, status, "unauthorized", "invalid API key")
		return false
	}
	if !tenant.ScopeAllows(key.Scope, requiredScope) {
		writeError(w, http.StatusForbidden, "forbidden", fmt.Sprintf("this endpoint requires the %q scope", requiredScope))
		return false
	}
	return true
}

type chatInput struct {
	Model    string    `json:"model,omitempty"`
	Messages []Message `json:"messages"`
	Stream   bool      `json:"stream,omitempty"`
}

func (s *Server) chat(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorize(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	var input chatInput
	if err := readJSON(w, r, &input); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if len(input.Messages) == 0 {
		writeError(w, http.StatusBadRequest, "invalid_request", "messages must not be empty")
		return
	}
	s.requests.Add(1)
	start := time.Now()

	// Semantic cache first: a near-duplicate prompt is answered without
	// touching the provider at all.
	if completion, score, ok := s.cache.Lookup(r.Context(), tenantID, input.Messages); ok {
		w.Header().Set("X-PolyForge-Cache", "hit")
		w.Header().Set("X-PolyForge-Cache-Score", strconv.FormatFloat(score, 'f', 4, 64))
		response := ChatResponse{
			Model:        "semantic-cache",
			FinishReason: "stop",
			Message:      Message{Role: "assistant", Content: completion},
		}
		if input.Stream {
			s.streamOut(w, response, true)
		} else {
			writeJSON(w, http.StatusOK, response)
		}
		// A hit still belongs to the AI latency family, so it carries the
		// tier it *would* have been served from; eval-export charges no
		// tier cost for cache-hit events.
		s.record(r.Context(), tenantID, "chat", input.Messages, completion, start, true, 1, s.tierFor(tenantID))
		return
	}
	w.Header().Set("X-PolyForge-Cache", "miss")

	// Routing happens only on a cache miss: a cached answer costs nothing,
	// so charging or routing it would distort the budget ledger.
	provider, backendName, tier := s.pickProvider(r.Context(), w, tenantID, input.Messages)

	req := ChatRequest{Model: input.Model, Messages: input.Messages}
	if input.Stream {
		flusher, ok := w.(http.Flusher)
		if !ok {
			writeError(w, http.StatusInternalServerError, "internal_error", "streaming unsupported by connection")
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.WriteHeader(http.StatusOK)
		response, err := provider.StreamChat(r.Context(), req, func(delta string) error {
			payload, err := json.Marshal(map[string]string{"delta": delta})
			if err != nil {
				return err
			}
			if _, err := fmt.Fprintf(w, "data: %s\n\n", payload); err != nil {
				return err
			}
			flusher.Flush()
			return nil
		})
		if err != nil {
			// Headers are gone; the best we can do is log and cut the stream.
			s.log.Error("stream chat failed mid-stream", "tenant", tenantID, "error", err)
			return
		}
		_, _ = fmt.Fprint(w, "data: [DONE]\n\n")
		flusher.Flush()
		s.chargeUsage(tenantID, backendName, response)
		s.cache.Store(r.Context(), tenantID, input.Messages, response.Message.Content)
		s.record(r.Context(), tenantID, "chat", input.Messages, response.Message.Content, start, false, 1, tier)
		return
	}

	response, err := provider.Chat(r.Context(), req)
	if err != nil {
		s.log.Error("chat provider failed", "tenant", tenantID, "backend", backendName, "error", err)
		writeError(w, http.StatusBadGateway, "upstream_error", "the model provider failed")
		return
	}
	s.chargeUsage(tenantID, backendName, response)
	s.cache.Store(r.Context(), tenantID, input.Messages, response.Message.Content)
	writeJSON(w, http.StatusOK, response)
	s.record(r.Context(), tenantID, "chat", input.Messages, response.Message.Content, start, false, 1, tier)
}

// tierFor is the tier a tenant's request is attributed to: the knob-pinned
// tier when one is set, the gateway-wide label otherwise.
func (s *Server) tierFor(tenantID string) string {
	if tier := s.knobTier(tenantID); tier != "" {
		return tier
	}
	return s.modelTier
}

// pickProvider resolves the backend for a cache miss. Order: the tenant's
// tier knob when it names a configured tier backend (the live tier lever,
// PREREG_WAVE4_LIVE_PLANE.md §Substrate 2), then the router policy, then
// the default provider. The decision is exposed in response headers so the
// routing is observable from the outside (and assertable in tests). The
// third return is the tier the request is served from, recorded per event
// so eval-export meters $-cost from the tier actually hit.
func (s *Server) pickProvider(ctx context.Context, w http.ResponseWriter, tenantID string, messages []Message) (Provider, string, string) {
	if tier := s.knobTier(tenantID); tier != "" {
		if provider, ok := s.tierProviders[tier]; ok {
			w.Header().Set("X-PolyForge-Backend", "tier:"+tier)
			w.Header().Set("X-PolyForge-Tier", tier)
			w.Header().Set("X-PolyForge-Route-Reason", "policy-tier")
			return provider, "tier:" + tier, tier
		}
	}
	if s.router == nil {
		return s.provider, "", s.modelTier
	}
	plan := ""
	if t, ok, err := s.tenants.Tenant(ctx, tenantID); err == nil && ok {
		plan = t.Plan
	}
	chars := 0
	for _, m := range messages {
		chars += len(m.Content)
	}
	decision := s.router.Route(tenantID, plan, chars)
	w.Header().Set("X-PolyForge-Backend", decision.Backend.Name)
	w.Header().Set("X-PolyForge-Route-Reason", decision.Reason)
	return decision.Backend.Provider, decision.Backend.Name, s.modelTier
}

func (s *Server) chargeUsage(tenantID, backendName string, response ChatResponse) {
	if s.router == nil || backendName == "" {
		return
	}
	s.router.RecordUsage(tenantID, backendName, response.PromptTokens, response.CompletionTokens)
}

// streamOut replays a completed response as a single-delta SSE stream so
// cached answers look identical to streamed ones on the wire.
func (s *Server) streamOut(w http.ResponseWriter, response ChatResponse, flush bool) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)
	payload, _ := json.Marshal(map[string]string{"delta": response.Message.Content})
	_, _ = fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", payload)
	if flusher, ok := w.(http.Flusher); ok && flush {
		flusher.Flush()
	}
}

type indexInput struct {
	ID   string            `json:"id"`
	Text string            `json:"text"`
	Meta map[string]string `json:"meta,omitempty"`
}

func (s *Server) indexDoc(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorize(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	var input indexInput
	if err := readJSON(w, r, &input); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if strings.TrimSpace(input.ID) == "" || strings.TrimSpace(input.Text) == "" {
		writeError(w, http.StatusBadRequest, "invalid_request", "id and text are required")
		return
	}
	vecs, err := s.embedder.Embed(r.Context(), []string{input.Text})
	if err != nil {
		writeError(w, http.StatusBadGateway, "upstream_error", "embedding failed")
		return
	}
	if err := s.search.Upsert(vector.Doc{
		ID: input.ID, TenantID: tenantID, Text: input.Text, Vector: vecs[0], Meta: input.Meta,
	}); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"id": input.ID, "indexed": true, "count": s.search.Count(tenantID)})
}

type searchInput struct {
	Query string `json:"query"`
	K     int    `json:"k,omitempty"`
}

func (s *Server) searchDocs(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorize(w, r, tenantID, tenant.ScopeRead) {
		return
	}
	var input searchInput
	if err := readJSON(w, r, &input); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if strings.TrimSpace(input.Query) == "" {
		writeError(w, http.StatusBadRequest, "invalid_request", "query is required")
		return
	}
	start := time.Now()
	vecs, err := s.embedder.Embed(r.Context(), []string{input.Query})
	if err != nil {
		writeError(w, http.StatusBadGateway, "upstream_error", "embedding failed")
		return
	}
	matches := s.search.Search(tenantID, vecs[0], input.K)
	writeJSON(w, http.StatusOK, map[string]any{"matches": matches})
	s.record(r.Context(), tenantID, "embed", nil, input.Query, start, false, 0, s.modelTier)
}

type agentInput struct {
	Prompt string `json:"prompt"`
}

func (s *Server) runAgent(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorize(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	if s.agent == nil {
		writeError(w, http.StatusNotImplemented, "not_configured", "no agent runner is configured")
		return
	}
	var input agentInput
	if err := readJSON(w, r, &input); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if strings.TrimSpace(input.Prompt) == "" {
		writeError(w, http.StatusBadRequest, "invalid_request", "prompt is required")
		return
	}
	s.requests.Add(1)
	start := time.Now()
	result, spans, err := s.agent(r.Context(), tenantID, input.Prompt)
	if err != nil {
		s.log.Error("agent run failed", "tenant", tenantID, "error", err)
		writeError(w, http.StatusBadGateway, "agent_error", "the agent run failed")
		return
	}
	writeJSON(w, http.StatusOK, result)
	s.record(r.Context(), tenantID, "agent", nil, input.Prompt, start, false, spans, s.tierFor(tenantID))
}

// record feeds the classifier: every AI request becomes a telemetry event,
// which is how the agentic-multistep and ai-cacheable workload classes
// become observable to the JCAC loop. The tier is per-request — the tier
// the request was actually served from (or would have been, for a cache
// hit) — so eval-export's $-metering follows the tier knob.
// `kind` must be one of the workload taxonomy's AI kinds — chat, embed or
// agent. It is recorded as the event's Service because that is the field the
// planner keys its demand on: operator/planner/demand.go maps any service
// outside the taxonomy to `crud_read`, so the previous literal "ai-gateway"
// made every AI request arrive at the planner as one work unit of CRUD —
// scored against the CRUD SLO target, with the cacheable fraction and the
// tier's per-request price both silently inapplicable.
func (s *Server) record(ctx context.Context, tenantID, kind string, messages []Message, completion string, start time.Time, cacheHit bool, childSpans int, tier string) {
	if s.telemetry == nil {
		return
	}
	payload := len(completion)
	for _, m := range messages {
		payload += len(m.Content)
	}
	_, err := s.telemetry.Add(ctx, telemetry.Event{
		TenantID:         tenantID,
		Service:          kind,
		Timestamp:        time.Now().UTC(),
		RPSWindow:        s.rates.Observe(tenantID + "|" + kind),
		PayloadBytes:     payload,
		LatencyMS:        float64(time.Since(start).Microseconds()) / 1000,
		CacheHit:         cacheHit,
		EmbeddingDensity: 1,
		ModelTier:        tier,
		ChildSpans:       childSpans,
	})
	if err != nil {
		s.log.Warn("record AI telemetry", "tenant", tenantID, "error", err)
	}
}

func readJSON(w http.ResponseWriter, r *http.Request, dst any) error {
	if r.Body == nil {
		return errors.New("request body is required")
	}
	defer func() { _ = r.Body.Close() }()
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	return decoder.Decode(dst)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{"error": map[string]string{"code": code, "message": message}})
}
