package platform

import (
	_ "embed"
	"errors"
	"net/http"

	"polyforge/internal/classifier/online"
)

// WorkloadReader serves the W27 admin surface from the online classifier's
// registry.
type WorkloadReader interface {
	Snapshot() ([]online.TenantWorkload, online.DriftStatus)
	History(tenantID string) []online.Sample
}

//go:embed workloads.html
var workloadsPage []byte

// adminWorkloads returns every tenant's current workload class plus the
// population drift status. Admin-key only: labels reveal cross-tenant
// behavior, which no tenant credential may see.
func (s *Server) adminWorkloads(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeAdmin(w, r) {
		return
	}
	if s.workloads == nil {
		s.writeError(w, r, http.StatusServiceUnavailable, errors.New("online classifier is not running"))
		return
	}
	tenants, drift := s.workloads.Snapshot()
	writeJSON(w, http.StatusOK, map[string]any{"tenants": tenants, "drift": drift})
}

func (s *Server) adminWorkloadHistory(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeAdmin(w, r) {
		return
	}
	if s.workloads == nil {
		s.writeError(w, r, http.StatusServiceUnavailable, errors.New("online classifier is not running"))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"tenant_id": r.PathValue("tenant_id"),
		"samples":   s.workloads.History(r.PathValue("tenant_id")),
	})
}

// workloadsUI serves the static single-file dashboard. The page itself
// carries no data; its JavaScript calls the admin API with the key the
// operator enters, so serving the shell unauthenticated leaks nothing.
func (s *Server) workloadsUI(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
	_, _ = w.Write(workloadsPage)
}
