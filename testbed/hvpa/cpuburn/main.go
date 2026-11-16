// Command cpuburn is the service of programme 2's WP17 (horizontal with
// vertical autoscaling; research/hvpa): every request burns a fixed amount of
// CPU, WORK_ROUNDS rounds of SHA-256 over a 4 KiB buffer, so a request's
// service time is set by the CPU the pod may use. Requests run concurrently,
// one goroutine each; the CPU limit (CFS quota) bounds the pod's throughput,
// and an in-place resize changes it without a restart (Go's runtime follows
// the cgroup limit for GOMAXPROCS).
//
//	GET /work     burn, then 200 with the wall time in X-Work-Seconds
//	GET /healthz  200 "ok"
//	GET /metrics  Prometheus text: cpuburn_arrivals_total (counted as a
//	              request starts: the offered rate, what the planner and KEDA
//	              read), cpuburn_requests_total (counted as it ends: the
//	              throughput, which saturated pods cap), cpuburn_in_flight
package main

import (
	"crypto/sha256"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync/atomic"
	"time"
)

const defaultRounds = 20000

type server struct {
	rounds   int
	arrivals atomic.Int64
	requests atomic.Int64
	inFlight atomic.Int64
	sink     atomic.Uint32 // keeps the compiler from dropping the work
}

// burn hashes a 4 KiB buffer `rounds` times, feeding each digest back in.
func burn(rounds int) uint32 {
	buf := make([]byte, 4096)
	var sum [32]byte
	for i := 0; i < rounds; i++ {
		copy(buf, sum[:])
		sum = sha256.Sum256(buf)
	}
	return uint32(sum[0]) | uint32(sum[1])<<8
}

func (s *server) work(w http.ResponseWriter, _ *http.Request) {
	s.arrivals.Add(1)
	s.inFlight.Add(1)
	defer s.inFlight.Add(-1)
	start := time.Now()
	s.sink.Add(burn(s.rounds))
	s.requests.Add(1)
	w.Header().Set("X-Work-Seconds", strconv.FormatFloat(time.Since(start).Seconds(), 'f', 6, 64))
	w.WriteHeader(http.StatusOK)
}

func (s *server) metrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	fmt.Fprintf(w, "# TYPE cpuburn_arrivals_total counter\ncpuburn_arrivals_total %d\n", s.arrivals.Load())
	fmt.Fprintf(w, "# TYPE cpuburn_requests_total counter\ncpuburn_requests_total %d\n", s.requests.Load())
	fmt.Fprintf(w, "# TYPE cpuburn_in_flight gauge\ncpuburn_in_flight %d\n", s.inFlight.Load())
}

func newMux(s *server) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /work", s.work)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("ok")) })
	mux.HandleFunc("GET /metrics", s.metrics)
	return mux
}

func rounds() int {
	if v, err := strconv.Atoi(os.Getenv("WORK_ROUNDS")); err == nil && v > 0 {
		return v
	}
	return defaultRounds
}

func main() {
	s := &server{rounds: rounds()}
	srv := &http.Server{Addr: ":8080", Handler: newMux(s), ReadHeaderTimeout: 5 * time.Second}
	log.Printf("cpuburn: %d rounds per request on :8080", s.rounds)
	log.Fatal(srv.ListenAndServe())
}
