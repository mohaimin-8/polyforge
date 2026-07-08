package platform

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net"
	"net/http"
	"regexp"
	"runtime/debug"
	"strconv"
	"strings"
	"sync"
	"time"

	"polyforge/internal/classifier"
	"polyforge/internal/controller"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

const maxRequestBodyBytes = 1 << 20

var resourceIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$`)
var errRateLimited = errors.New("rate limit exceeded")

type Server struct {
	log       *slog.Logger
	tenants   tenant.Repository
	telemetry telemetry.Repository
	adminKey  string
	mux       *http.ServeMux
	limiter   *rateLimiter
	metrics   *metrics
}

type Config struct {
	AdminKey  string
	RateLimit RateLimitConfig
}

type RateLimitConfig struct {
	RequestsPerMinute int
	Burst             int
}

type errorEnvelope struct {
	Error apiError `json:"error"`
}

type apiError struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	RequestID string `json:"request_id"`
}

type contextKey string

const requestIDKey contextKey = "request_id"
const traceContextKey contextKey = "trace_context"

func NewServer(log *slog.Logger, tenants tenant.Repository, telemetry telemetry.Repository, cfg Config) *Server {
	s := &Server{
		log:       log,
		tenants:   tenants,
		telemetry: telemetry,
		adminKey:  cfg.AdminKey,
		mux:       http.NewServeMux(),
		limiter:   newRateLimiter(cfg.RateLimit),
		metrics:   newMetrics(),
	}
	s.routes()
	return s
}

func (s *Server) Handler() http.Handler {
	handler := s.recoverPanics(s.mux)
	if s.limiter != nil {
		handler = s.rateLimit(handler)
	}
	return requestLog(s.log, s.metrics, handler)
}

// recoverPanics converts a handler panic into a structured 500 response so a
// single failing request cannot drop the connection without a request ID the
// client can report. It sits inside requestLog, so the 500 still reaches the
// request log and metrics.
func (s *Server) recoverPanics(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			value := recover()
			if value == nil {
				return
			}
			// net/http uses this sentinel to abort a response on purpose.
			if err, ok := value.(error); ok && errors.Is(err, http.ErrAbortHandler) {
				panic(value)
			}
			s.log.Error("handler panic recovered",
				"panic", fmt.Sprint(value),
				"stack", string(debug.Stack()),
				"method", r.Method,
				"path", r.URL.Path,
				"request_id", requestID(r.Context()),
			)
			s.writeError(w, r, http.StatusInternalServerError, errors.New("internal server error"))
		}()
		next.ServeHTTP(w, r)
	})
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("GET /metrics", s.metricsEndpoint)
	s.mux.HandleFunc("GET /v1/tenants", s.listTenants)
	s.mux.HandleFunc("POST /v1/tenants", s.createTenant)
	s.mux.HandleFunc("GET /v1/tenants/{tenant_id}/api-keys", s.listAPIKeys)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/api-keys", s.createAPIKey)
	s.mux.HandleFunc("DELETE /v1/tenants/{tenant_id}/api-keys/{key_id}", s.revokeAPIKey)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/api-keys/{key_id}/rotate", s.rotateAPIKey)
	s.mux.HandleFunc("GET /v1/tenants/{tenant_id}/projects", s.listProjects)
	s.mux.HandleFunc("POST /v1/tenants/{tenant_id}/projects", s.createProject)
	s.mux.HandleFunc("GET /v1/tenants/{tenant_id}/projects/{project_id}", s.getProject)
	s.mux.HandleFunc("PATCH /v1/tenants/{tenant_id}/projects/{project_id}", s.updateProject)
	s.mux.HandleFunc("DELETE /v1/tenants/{tenant_id}/projects/{project_id}", s.deleteProject)
	s.mux.HandleFunc("POST /v1/telemetry", s.ingestTelemetry)
	s.mux.HandleFunc("GET /v1/tenants/{tenant_id}/telemetry/features", s.telemetryFeatures)
	s.mux.HandleFunc("GET /v1/tenants/{tenant_id}/workload-profile", s.workloadProfile)
	s.mux.HandleFunc("GET /v1/tenants/{tenant_id}/policy-recommendation", s.policyRecommendation)
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "service": "polyforge-control-plane"})
}

func (s *Server) metricsEndpoint(w http.ResponseWriter, _ *http.Request) {
	s.metrics.WritePrometheus(w)
}

func (s *Server) listTenants(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeAdmin(w, r) {
		return
	}
	tenants, err := s.tenants.ListTenants(r.Context())
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusOK, tenants)
}

func (s *Server) createTenant(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeAdmin(w, r) {
		return
	}
	var input tenant.Tenant
	if err := readJSON(w, r, &input); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	if !validResourceID(input.ID) || !validName(input.Name) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("id must be 1-63 safe characters and name must be 1-200 characters"))
		return
	}
	if input.IsolationMode != "" && input.IsolationMode != "pool" && input.IsolationMode != "bridge" && input.IsolationMode != "silo" {
		s.writeError(w, r, http.StatusBadRequest, errors.New("isolation_mode must be pool, bridge, or silo"))
		return
	}
	created, key, err := s.tenants.ProvisionTenant(r.Context(), input, "bootstrap")
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"tenant": created, "bootstrap_api_key": key})
}

func (s *Server) listAPIKeys(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeRead) {
		return
	}
	keys, err := s.tenants.ListAPIKeys(r.Context(), tenantID)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusOK, keys)
}

func (s *Server) createAPIKey(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	var input struct {
		Name  string `json:"name"`
		Scope string `json:"scope"`
	}
	if err := readJSON(w, r, &input); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	if !validName(input.Name) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("name must be 1-200 characters"))
		return
	}
	if !tenant.ValidScope(input.Scope) {
		s.writeError(w, r, http.StatusBadRequest, errors.New(`scope must be "read" or "full"`))
		return
	}
	key, err := s.tenants.CreateAPIKey(r.Context(), tenantID, input.Name, input.Scope)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusCreated, key)
}

func (s *Server) revokeAPIKey(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	if err := s.tenants.RevokeAPIKey(r.Context(), tenantID, r.PathValue("key_id")); err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) rotateAPIKey(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	var input struct {
		Name string `json:"name"`
	}
	if err := readOptionalJSON(w, r, &input); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	if input.Name != "" && !validName(input.Name) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("name must be at most 200 characters"))
		return
	}
	key, err := s.tenants.RotateAPIKey(r.Context(), tenantID, r.PathValue("key_id"), input.Name)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusCreated, key)
}

func (s *Server) listProjects(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeRead) {
		return
	}
	page := tenant.PageRequest{Cursor: r.URL.Query().Get("cursor")}
	if raw := r.URL.Query().Get("limit"); raw != "" {
		limit, err := strconv.Atoi(raw)
		if err != nil || limit < 1 || limit > tenant.MaxPageSize {
			s.writeError(w, r, http.StatusBadRequest, errors.New("limit must be an integer between 1 and 200"))
			return
		}
		page.Limit = limit
	}
	projects, err := s.tenants.ListProjects(r.Context(), tenantID, page)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusOK, projects)
}

func (s *Server) createProject(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	var input tenant.Project
	if err := readJSON(w, r, &input); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	if !validResourceID(input.ID) || !validName(input.Name) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("id must be 1-63 safe characters and name must be 1-200 characters"))
		return
	}
	input.TenantID = tenantID
	project, err := s.tenants.CreateProject(r.Context(), input)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusCreated, project)
}

func (s *Server) getProject(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeRead) {
		return
	}
	project, err := s.tenants.Project(r.Context(), tenantID, r.PathValue("project_id"))
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusOK, project)
}

func (s *Server) updateProject(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	var input struct {
		Name string `json:"name"`
	}
	if err := readJSON(w, r, &input); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	if !validName(input.Name) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("name must be 1-200 characters"))
		return
	}
	project, err := s.tenants.UpdateProject(r.Context(), tenant.Project{
		ID:       r.PathValue("project_id"),
		TenantID: tenantID,
		Name:     input.Name,
	})
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusOK, project)
}

func (s *Server) deleteProject(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeFull) {
		return
	}
	if err := s.tenants.DeleteProject(r.Context(), tenantID, r.PathValue("project_id")); err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) ingestTelemetry(w http.ResponseWriter, r *http.Request) {
	var event telemetry.Event
	if err := readJSON(w, r, &event); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	if !validResourceID(event.TenantID) || !validName(event.Service) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("tenant_id and service are required"))
		return
	}
	if event.RPSWindow < 0 || event.PayloadBytes < 0 || event.LatencyMS < 0 || event.EmbeddingDensity < 0 || event.EmbeddingDensity > 1 || event.ChildSpans < 0 {
		s.writeError(w, r, http.StatusBadRequest, errors.New("telemetry values are outside their valid ranges"))
		return
	}
	if !s.authorizeTenant(w, r, event.TenantID, tenant.ScopeFull) {
		return
	}
	event, err := s.telemetry.Add(r.Context(), event)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	s.metrics.RecordTelemetryEvent(event)
	writeJSON(w, http.StatusAccepted, event)
}

func (s *Server) telemetryFeatures(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeRead) {
		return
	}
	if _, ok, err := s.tenants.Tenant(r.Context(), tenantID); err != nil {
		s.writeError(w, r, http.StatusInternalServerError, err)
		return
	} else if !ok {
		s.writeError(w, r, http.StatusNotFound, tenant.ErrTenantNotFound)
		return
	}

	params := r.URL.Query()
	query := telemetry.FeatureQuery{Service: strings.TrimSpace(params.Get("service"))}
	if query.Service != "" && !validName(query.Service) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("service must be 1-200 characters"))
		return
	}
	since, err := parseTimestampParam(params.Get("since"))
	if err != nil {
		s.writeError(w, r, http.StatusBadRequest, errors.New("since must be an RFC 3339 timestamp"))
		return
	}
	until, err := parseTimestampParam(params.Get("until"))
	if err != nil {
		s.writeError(w, r, http.StatusBadRequest, errors.New("until must be an RFC 3339 timestamp"))
		return
	}
	if !since.IsZero() && !until.IsZero() && !since.Before(until) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("since must be strictly before until"))
		return
	}
	query.Since = since
	query.Until = until

	features, err := s.telemetry.Features(r.Context(), tenantID, query)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	writeJSON(w, http.StatusOK, features)
}

// parseTimestampParam accepts an empty value as "unset" so the repository layer
// can apply its default lookback window.
func parseTimestampParam(raw string) (time.Time, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return time.Time{}, nil
	}
	parsed, err := time.Parse(time.RFC3339, raw)
	if err != nil {
		return time.Time{}, err
	}
	return parsed.UTC(), nil
}

func (s *Server) workloadProfile(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeRead) {
		return
	}
	if _, ok, err := s.tenants.Tenant(r.Context(), tenantID); err != nil {
		s.writeError(w, r, http.StatusInternalServerError, err)
		return
	} else if !ok {
		s.writeError(w, r, http.StatusNotFound, tenant.ErrTenantNotFound)
		return
	}
	events, err := s.telemetry.RecentByTenant(r.Context(), tenantID, 100)
	if err != nil {
		s.writeError(w, r, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, http.StatusOK, classifier.Classify(tenantID, events))
}

func (s *Server) policyRecommendation(w http.ResponseWriter, r *http.Request) {
	tenantID := r.PathValue("tenant_id")
	if !s.authorizeTenant(w, r, tenantID, tenant.ScopeRead) {
		return
	}
	if _, ok, err := s.tenants.Tenant(r.Context(), tenantID); err != nil {
		s.writeError(w, r, http.StatusInternalServerError, err)
		return
	} else if !ok {
		s.writeError(w, r, http.StatusNotFound, tenant.ErrTenantNotFound)
		return
	}
	events, err := s.telemetry.RecentByTenant(r.Context(), tenantID, 100)
	if err != nil {
		s.writeError(w, r, http.StatusInternalServerError, err)
		return
	}
	result := classifier.Classify(tenantID, events)
	writeJSON(w, http.StatusOK, controller.Recommend(result))
}

// authorizeTenant authenticates the tenant API key and enforces its scope.
// Authentication failures are 401; a valid key without the required scope is
// 403 so the caller can distinguish a bad credential from missing authority.
func (s *Server) authorizeTenant(w http.ResponseWriter, r *http.Request, tenantID, requiredScope string) bool {
	secret := r.Header.Get("X-PolyForge-API-Key")
	if strings.TrimSpace(secret) == "" {
		s.writeError(w, r, http.StatusUnauthorized, tenant.ErrUnauthorized)
		return false
	}
	key, err := s.tenants.AuthenticateAPIKey(r.Context(), tenantID, secret)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return false
	}
	if !tenant.ScopeAllows(key.Scope, requiredScope) {
		s.writeError(w, r, http.StatusForbidden,
			fmt.Errorf("%w: this endpoint requires the %q scope", tenant.ErrForbidden, requiredScope))
		return false
	}
	return true
}

func (s *Server) authorizeAdmin(w http.ResponseWriter, r *http.Request) bool {
	provided := r.Header.Get("X-PolyForge-Admin-Key")
	if s.adminKey == "" || len(provided) != len(s.adminKey) || subtle.ConstantTimeCompare([]byte(provided), []byte(s.adminKey)) != 1 {
		s.writeError(w, r, http.StatusUnauthorized, tenant.ErrUnauthorized)
		return false
	}
	return true
}

func readJSON(w http.ResponseWriter, r *http.Request, dst any) error {
	if r.Body == nil {
		return errors.New("request body is required")
	}
	defer func() { _ = r.Body.Close() }()
	r.Body = http.MaxBytesReader(w, r.Body, maxRequestBodyBytes)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(dst); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("request body must contain a single JSON object")
	}
	return nil
}

func readOptionalJSON(w http.ResponseWriter, r *http.Request, dst any) error {
	if r.Body == nil || r.ContentLength == 0 {
		return nil
	}
	return readJSON(w, r, dst)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func (s *Server) writeError(w http.ResponseWriter, r *http.Request, status int, err error) {
	message := err.Error()
	if status >= http.StatusInternalServerError {
		message = "internal server error"
		trace := traceFromContext(r.Context())
		s.log.Error("request failed",
			"request_id", requestID(r.Context()),
			"trace_id", trace.TraceID,
			"span_id", trace.SpanID,
			"error", err,
		)
	}
	writeJSON(w, status, errorEnvelope{Error: apiError{
		Code:      errorCode(status, err),
		Message:   message,
		RequestID: requestID(r.Context()),
	}})
}

func statusFor(err error) int {
	switch {
	case errors.Is(err, tenant.ErrTenantNotFound), errors.Is(err, tenant.ErrProjectNotFound), errors.Is(err, tenant.ErrAPIKeyNotFound):
		return http.StatusNotFound
	case errors.Is(err, tenant.ErrUnauthorized):
		return http.StatusUnauthorized
	case errors.Is(err, tenant.ErrForbidden):
		return http.StatusForbidden
	case errors.Is(err, tenant.ErrConflict):
		return http.StatusConflict
	default:
		return http.StatusInternalServerError
	}
}

func errorCode(status int, err error) string {
	switch {
	case errors.Is(err, tenant.ErrTenantNotFound):
		return "tenant_not_found"
	case errors.Is(err, tenant.ErrProjectNotFound):
		return "project_not_found"
	case errors.Is(err, tenant.ErrAPIKeyNotFound):
		return "api_key_not_found"
	case errors.Is(err, tenant.ErrUnauthorized):
		return "unauthorized"
	case errors.Is(err, tenant.ErrForbidden):
		return "forbidden"
	case errors.Is(err, tenant.ErrConflict):
		return "conflict"
	case errors.Is(err, errRateLimited), status == http.StatusTooManyRequests:
		return "rate_limited"
	case status == http.StatusBadRequest:
		return "invalid_request"
	default:
		return "internal_error"
	}
}

func validResourceID(value string) bool {
	return resourceIDPattern.MatchString(strings.TrimSpace(value))
}

func validName(value string) bool {
	length := len(strings.TrimSpace(value))
	return length > 0 && length <= 200
}

type statusWriter struct {
	http.ResponseWriter
	status int
	bytes  int
}

func (w *statusWriter) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *statusWriter) Write(value []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	n, err := w.ResponseWriter.Write(value)
	w.bytes += n
	return n, err
}

func requestLog(log *slog.Logger, metrics *metrics, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		id := newRequestID()
		trace := newTraceContext(r.Header.Get("traceparent"))
		ctx := context.WithValue(r.Context(), requestIDKey, id)
		ctx = context.WithValue(ctx, traceContextKey, trace)
		r = r.WithContext(ctx)
		w.Header().Set("X-Request-ID", id)
		w.Header().Set("traceparent", trace.TraceParent())
		recorder := &statusWriter{ResponseWriter: w}
		next.ServeHTTP(recorder, r)
		if recorder.status == 0 {
			recorder.status = http.StatusOK
		}
		duration := time.Since(start)
		route := routePattern(r)
		metrics.RecordHTTPRequest(r.Method, route, recorder.status, duration)
		log.Info("request",
			"request_id", id,
			"trace_id", trace.TraceID,
			"span_id", trace.SpanID,
			"parent_span_id", trace.ParentSpanID,
			"trace_remote", trace.Remote,
			"method", r.Method,
			"route", route,
			"path", r.URL.Path,
			"status", recorder.status,
			"response_bytes", recorder.bytes,
			"duration_ms", duration.Milliseconds(),
		)
	})
}

func routePattern(r *http.Request) string {
	if r.Pattern != "" {
		if _, route, ok := strings.Cut(r.Pattern, " "); ok {
			return route
		}
		return r.Pattern
	}
	return r.URL.Path
}

func newRequestID() string {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 36)
	}
	return hex.EncodeToString(value)
}

func requestID(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey).(string)
	return id
}

func traceFromContext(ctx context.Context) traceContext {
	trace, _ := ctx.Value(traceContextKey).(traceContext)
	return trace
}

func (s *Server) rateLimit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" || r.URL.Path == "/metrics" {
			next.ServeHTTP(w, r)
			return
		}
		ok, retryAfter := s.limiter.Allow(rateLimitKey(r))
		if !ok {
			w.Header().Set("Retry-After", strconv.Itoa(max(1, int(math.Ceil(retryAfter.Seconds())))))
			s.writeError(w, r, http.StatusTooManyRequests, errRateLimited)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func rateLimitKey(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil || host == "" {
		host = r.RemoteAddr
	}
	if r.URL.Path == "/v1/tenants" {
		return "admin:" + host
	}
	if secret := strings.TrimSpace(r.Header.Get("X-PolyForge-Admin-Key")); secret != "" {
		return "admin:" + secretFingerprint(secret)
	}
	if secret := strings.TrimSpace(r.Header.Get("X-PolyForge-API-Key")); secret != "" {
		return "tenant:" + secretFingerprint(secret)
	}
	return "anon:" + host
}

func secretFingerprint(secret string) string {
	sum := sha256.Sum256([]byte(secret))
	return hex.EncodeToString(sum[:8])
}

type rateLimiter struct {
	mu              sync.Mutex
	buckets         map[string]*rateBucket
	capacity        float64
	refillPerSecond float64
	clock           func() time.Time
	lastPrune       time.Time
}

type rateBucket struct {
	tokens   float64
	updated  time.Time
	lastSeen time.Time
}

func newRateLimiter(cfg RateLimitConfig) *rateLimiter {
	if cfg.RequestsPerMinute <= 0 || cfg.Burst <= 0 {
		return nil
	}
	return &rateLimiter{
		buckets:         make(map[string]*rateBucket),
		capacity:        float64(cfg.Burst),
		refillPerSecond: float64(cfg.RequestsPerMinute) / 60,
		clock:           time.Now,
	}
}

func (l *rateLimiter) Allow(key string) (bool, time.Duration) {
	now := l.clock()
	l.mu.Lock()
	defer l.mu.Unlock()

	if l.lastPrune.IsZero() {
		l.lastPrune = now
	} else if now.Sub(l.lastPrune) > 10*time.Minute {
		l.prune(now)
		l.lastPrune = now
	}

	bucket := l.buckets[key]
	if bucket == nil {
		bucket = &rateBucket{tokens: l.capacity, updated: now}
		l.buckets[key] = bucket
	}

	elapsed := now.Sub(bucket.updated).Seconds()
	if elapsed > 0 {
		bucket.tokens = math.Min(l.capacity, bucket.tokens+elapsed*l.refillPerSecond)
		bucket.updated = now
	}
	bucket.lastSeen = now

	if bucket.tokens < 1 {
		return false, time.Duration(math.Ceil((1-bucket.tokens)/l.refillPerSecond)) * time.Second
	}
	bucket.tokens--
	return true, 0
}

func (l *rateLimiter) prune(now time.Time) {
	for key, bucket := range l.buckets {
		if now.Sub(bucket.lastSeen) > 30*time.Minute {
			delete(l.buckets, key)
		}
	}
}
