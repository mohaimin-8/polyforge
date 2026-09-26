package egress_test

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"polyforge/internal/ai/egress"
)

func TestClientRefusesEveryRedirect(t *testing.T) {
	for _, code := range []int{http.StatusMovedPermanently, http.StatusFound, http.StatusSeeOther,
		http.StatusTemporaryRedirect, http.StatusPermanentRedirect} {
		hostile := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "http://169.254.169.254/latest/meta-data/", code)
		}))
		resp, err := egress.Client(5 * time.Second).Get(hostile.URL)
		hostile.Close()
		if err == nil {
			_ = resp.Body.Close()
			t.Fatalf("status %d: client followed a redirect", code)
		}
	}
}

func TestClientReturnsDirectResponses(t *testing.T) {
	ok := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer ok.Close()
	resp, err := egress.Client(5 * time.Second).Get(ok.URL)
	if err != nil {
		t.Fatalf("direct request failed: %v", err)
	}
	_ = resp.Body.Close()
}
