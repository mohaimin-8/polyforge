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
	log       *slog.Logger
	provider  Provider
	cache     *SemanticCache
	embedder  embed.Embedder
	search    *vector.Index
	tenants   tenant.Repository
	telemetry telemetry.Repository
	agent     AgentFunc
	modelTier string
	mux       *http.ServeMux
	requests  atomic.Int64
}

type Config struct {
	Provider Provider
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
}

func NewServer(log *slog.Logger, cfg Config) *Server {
	if log == nil {
		log = slog.Default()
	}
	if cfg.ModelTier == "" {
		cfg.ModelTier = "mid"
	}
	s := &Server{
		log:       log,
		provider:  cfg.Provider,
		cache:     NewSemanticCache(cfg.Embedder, cfg.CacheThreshold),
		embedder:  cfg.Embedder,
		search:    vector.NewIndex(cfg.Embedder.Dimensions()),
		tenants:   cfg.Tenants,
		telemetry: cfg.Telemetry,
		agent:     cfg.Agent,
		modelTier: cfg.ModelTier,
		mux:       http.NewServeMux(),
	}
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("GET /metrics", s.metrics)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/ai/chat", s.chat)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/ai/index", s.indexDoc)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/ai/search", s.searchDocs)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/ai/agent", s.runAgent)
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
		s.record(r.Context(), tenantID, input.Messages, completion, start, true, 1)
		return
	}
	w.Header().Set("X-PolyForge-Cache", "miss")

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
		response, err := s.provider.StreamChat(r.Context(), req, func(delta string) error {
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
		s.cache.Store(r.Context(), tenantID, input.Messages, response.Message.Content)
		s.record(r.Context(), tenantID, input.Messages, response.Message.Content, start, false, 1)
		return
	}

	response, err := s.provider.Chat(r.Context(), req)
	if err != nil {
		s.log.Error("chat provider failed", "tenant", tenantID, "error", err)
		writeError(w, http.StatusBadGateway, "upstream_error", "the model provider failed")
		return
	}
	s.cache.Store(r.Context(), tenantID, input.Messages, response.Message.Content)
	writeJSON(w, http.StatusOK, response)
	s.record(r.Context(), tenantID, input.Messages, response.Message.Content, start, false, 1)
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
	s.record(r.Context(), tenantID, nil, input.Query, start, false, 0)
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
	s.record(r.Context(), tenantID, nil, input.Prompt, start, false, spans)
}

// record feeds the classifier: every AI request becomes a telemetry event,
// which is how the agentic-multistep and ai-cacheable workload classes
// become observable to the JCAC loop.
func (s *Server) record(ctx context.Context, tenantID string, messages []Message, completion string, start time.Time, cacheHit bool, childSpans int) {
	if s.telemetry == nil {
		return
	}
	payload := len(completion)
	for _, m := range messages {
		payload += len(m.Content)
	}
	_, err := s.telemetry.Add(ctx, telemetry.Event{
		TenantID:         tenantID,
		Service:          "ai-gateway",
		Timestamp:        time.Now().UTC(),
		PayloadBytes:     payload,
		LatencyMS:        float64(time.Since(start).Microseconds()) / 1000,
		CacheHit:         cacheHit,
		EmbeddingDensity: 1,
		ModelTier:        s.modelTier,
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
