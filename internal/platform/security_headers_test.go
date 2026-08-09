package platform

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// Session 36: the first OWASP ZAP run against the real API surface reported
// these three headers missing on every endpoint. They must be present on
// success AND error paths, which is why the middleware wraps the whole mux.
func TestSecurityHeadersOnEveryResponse(t *testing.T) {
	want := map[string]string{
		"X-Content-Type-Options":       "nosniff",
		"Cross-Origin-Resource-Policy": "same-origin",
		"Cache-Control":                "no-store",
	}

	for _, tc := range []struct {
		name    string
		handler http.Handler
	}{
		{"success", http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusOK)
		})},
		{"error", http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusInternalServerError)
		})},
		{"not-found", http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusNotFound)
		})},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			securityHeaders(tc.handler).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/x", nil))
			for header, value := range want {
				if got := rec.Header().Get(header); got != value {
					t.Errorf("%s = %q, want %q", header, got, value)
				}
			}
		})
	}
}
