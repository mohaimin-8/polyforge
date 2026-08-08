package gateway

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// A provider client must not follow a redirect an upstream returns — the
// SSRF-by-redirect vector (302 to cloud metadata or an internal service).
func TestEgressClientRefusesRedirect(t *testing.T) {
	// A hostile "provider" that 302s to somewhere it should never reach.
	hostile := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "http://169.254.169.254/latest/meta-data/", http.StatusFound)
	}))
	defer hostile.Close()

	resp, err := egressClient(5 * time.Second).Get(hostile.URL)
	if err == nil {
		_ = resp.Body.Close()
		t.Fatal("client followed a redirect; egress guard not active")
	}
}

// A non-redirect response is returned normally.
func TestEgressClientAllowsDirectResponse(t *testing.T) {
	ok := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer ok.Close()

	resp, err := egressClient(5 * time.Second).Get(ok.URL)
	if err != nil {
		t.Fatalf("direct request failed: %v", err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}
