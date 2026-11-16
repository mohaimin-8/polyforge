package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
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
	if !strings.Contains(string(body), "cpuburn_requests_total 3\n") || !strings.Contains(string(body), "cpuburn_in_flight 0\n") ||
		!strings.Contains(string(body), "cpuburn_arrivals_total 3\n") {
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

// A request is an arrival as it starts and a completion as it ends: the planner
// and KEDA read arrivals, the offered rate, which completions under-read when
// the pods are saturated.
func TestArrivalIsCountedBeforeTheWork(t *testing.T) {
	ts := httptest.NewServer(newMux(&server{rounds: 400000}))
	defer ts.Close()
	done := make(chan error, 1)
	go func() {
		resp, err := http.Get(ts.URL + "/work")
		if err == nil {
			resp.Body.Close()
		}
		done <- err
	}()
	deadline := time.Now().Add(5 * time.Second)
	for {
		resp, err := http.Get(ts.URL + "/metrics")
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if strings.Contains(string(body), "cpuburn_arrivals_total 1") {
			if !strings.Contains(string(body), "cpuburn_requests_total 0") {
				t.Fatalf("the request finished before it was seen in flight: %q", body)
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("no arrival counted: %q", body)
		}
		time.Sleep(5 * time.Millisecond)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
