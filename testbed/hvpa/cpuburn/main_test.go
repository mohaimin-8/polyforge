package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
)

func TestBurnIsDeterministic(t *testing.T) {
	if burn(50) != burn(50) {
		t.Fatal("same rounds, different result")
	}
	if burn(50) == burn(51) {
		t.Fatal("one more round should change the digest")
	}
}

func TestWorkCountsAndReportsItsTime(t *testing.T) {
	s := &server{rounds: 10}
	ts := httptest.NewServer(newMux(s))
	defer ts.Close()
	for i := 0; i < 3; i++ {
		resp, err := http.Get(ts.URL + "/work")
		if err != nil {
			t.Fatal(err)
		}
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("status %d", resp.StatusCode)
		}
		if _, err := strconv.ParseFloat(resp.Header.Get("X-Work-Seconds"), 64); err != nil {
			t.Fatalf("X-Work-Seconds: %v", err)
		}
		resp.Body.Close()
	}
	resp, err := http.Get(ts.URL + "/metrics")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if !strings.Contains(string(body), "cpuburn_requests_total 3\n") || !strings.Contains(string(body), "cpuburn_in_flight 0\n") {
		t.Fatalf("metrics: %q", body)
	}
}

func TestOnlyGetIsServed(t *testing.T) {
	ts := httptest.NewServer(newMux(&server{rounds: 1}))
	defer ts.Close()
	resp, err := http.Post(ts.URL+"/work", "text/plain", nil)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("POST /work -> %d", resp.StatusCode)
	}
}

func TestRoundsFromTheEnvironment(t *testing.T) {
	t.Setenv("WORK_ROUNDS", "123")
	if rounds() != 123 {
		t.Fatal("WORK_ROUNDS not read")
	}
	t.Setenv("WORK_ROUNDS", "nonsense")
	if rounds() != defaultRounds {
		t.Fatal("a bad WORK_ROUNDS must fall back to the default")
	}
}
