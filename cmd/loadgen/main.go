// Command loadgen is PolyForge's HTTP load generator. It drives a single
// endpoint with a fixed-size worker pool for a fixed duration and writes a
// JSON artifact with throughput and latency percentiles, so benchmark runs
// are reproducible and comparable across machines and commits.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"
)

type workerResult struct {
	latencies []float64 // milliseconds, one per completed request
	non2xx    int
	errors    int
}

type artifact struct {
	GeneratedAt     string             `json:"generated_at"`
	TargetURL       string             `json:"target_url"`
	DurationSeconds float64            `json:"duration_seconds"`
	Concurrency     int                `json:"concurrency"`
	TargetRate      int                `json:"target_rate,omitempty"`
	TotalRequests   int                `json:"total_requests"`
	RequestsPerSec  float64            `json:"requests_per_second"`
	Non2xx          int                `json:"non_2xx_responses"`
	TransportErrors int                `json:"transport_errors"`
	LatencyMS       map[string]float64 `json:"latency_ms"`
	Runtime         map[string]any     `json:"runtime"`
}

func main() {
	url := flag.String("url", "http://localhost:8080/healthz", "target URL to load")
	duration := flag.Duration("duration", 15*time.Second, "how long to generate load")
	concurrency := flag.Int("concurrency", 2*runtime.NumCPU(), "number of worker goroutines")
	warmup := flag.Duration("warmup", 2*time.Second, "load to apply and discard before measuring")
	rate := flag.Int("rate", 0, "target total requests/sec across all workers (0 = unthrottled)")
	out := flag.String("out", "", "path for the JSON artifact (omit to skip writing)")
	headers := flag.String("headers", "", `extra request headers, semicolon-separated ("K: V; K2: V2")`)
	flag.Parse()

	if *concurrency < 1 || *duration <= 0 {
		fmt.Fprintln(os.Stderr, "concurrency must be >= 1 and duration must be positive")
		os.Exit(2)
	}

	client := &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http.Transport{
			MaxIdleConns:        *concurrency,
			MaxIdleConnsPerHost: *concurrency,
			IdleConnTimeout:     90 * time.Second,
		},
	}
	request, err := http.NewRequest(http.MethodGet, *url, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid url:", err)
		os.Exit(2)
	}
	for _, header := range strings.Split(*headers, ";") {
		if name, value, ok := strings.Cut(header, ":"); ok {
			request.Header.Set(strings.TrimSpace(name), strings.TrimSpace(value))
		}
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()

	if *warmup > 0 {
		warmupCtx, cancel := context.WithTimeout(ctx, *warmup)
		runPool(warmupCtx, client, request, *concurrency, *rate)
		cancel()
	}
	if ctx.Err() != nil {
		fmt.Fprintln(os.Stderr, "interrupted during warmup")
		os.Exit(1)
	}

	runCtx, cancel := context.WithTimeout(ctx, *duration)
	defer cancel()
	started := time.Now()
	results := runPool(runCtx, client, request, *concurrency, *rate)
	elapsed := time.Since(started)

	var latencies []float64
	non2xx, transportErrors := 0, 0
	for _, result := range results {
		latencies = append(latencies, result.latencies...)
		non2xx += result.non2xx
		transportErrors += result.errors
	}
	sort.Float64s(latencies)

	report := artifact{
		GeneratedAt:     time.Now().UTC().Format(time.RFC3339),
		TargetURL:       *url,
		DurationSeconds: round2(elapsed.Seconds()),
		Concurrency:     *concurrency,
		TargetRate:      *rate,
		TotalRequests:   len(latencies),
		RequestsPerSec:  round2(float64(len(latencies)) / elapsed.Seconds()),
		Non2xx:          non2xx,
		TransportErrors: transportErrors,
		LatencyMS: map[string]float64{
			"p50": percentile(latencies, 50),
			"p90": percentile(latencies, 90),
			"p95": percentile(latencies, 95),
			"p99": percentile(latencies, 99),
			"max": percentile(latencies, 100),
		},
		Runtime: map[string]any{
			"os":         runtime.GOOS,
			"arch":       runtime.GOARCH,
			"cpus":       runtime.NumCPU(),
			"go_version": runtime.Version(),
		},
	}

	encoded, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "encode artifact:", err)
		os.Exit(1)
	}
	fmt.Println(string(encoded))
	if *out != "" {
		if err := os.MkdirAll(filepath.Dir(*out), 0o755); err != nil {
			fmt.Fprintln(os.Stderr, "create artifact directory:", err)
			os.Exit(1)
		}
		if err := os.WriteFile(*out, append(encoded, '\n'), 0o644); err != nil {
			fmt.Fprintln(os.Stderr, "write artifact:", err)
			os.Exit(1)
		}
	}
	if transportErrors > 0 || non2xx > 0 {
		os.Exit(1)
	}
}

// runPool fans the request out over size workers until ctx is done. Each
// worker records into its own slice so the hot path takes no locks; results
// are merged after every worker has returned over an unbuffered channel.
func runPool(ctx context.Context, client *http.Client, request *http.Request, size, rate int) []workerResult {
	// rate > 0 paces the pool with a shared token dispatcher (the W16 gate
	// shape: sustain a target aggregate RPS instead of running flat out).
	// Tokens are minted from measured elapsed time, so coarse OS timer
	// granularity cannot silently under-deliver the rate; the buffer bounds
	// how large a burst can form when workers fall behind.
	var tokens chan struct{}
	if rate > 0 {
		tokens = make(chan struct{}, max(1, rate/10))
		go func() {
			ticker := time.NewTicker(5 * time.Millisecond)
			defer ticker.Stop()
			var accumulated float64
			last := time.Now()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
				}
				now := time.Now()
				accumulated += float64(rate) * now.Sub(last).Seconds()
				last = now
				for accumulated >= 1 {
					select {
					case tokens <- struct{}{}:
					default: // workers are saturated; shed instead of bursting later
					}
					accumulated--
				}
			}
		}()
	}
	resultCh := make(chan workerResult)
	for worker := 0; worker < size; worker++ {
		go func() {
			var result workerResult
			for ctx.Err() == nil {
				if tokens != nil {
					select {
					case <-tokens:
					case <-ctx.Done():
					}
				}
				if ctx.Err() != nil {
					break
				}
				started := time.Now()
				resp, err := client.Do(request.Clone(ctx))
				if err != nil {
					// A cancellation mid-request is shutdown, not a failure.
					if ctx.Err() == nil {
						result.errors++
					}
					continue
				}
				_, _ = io.Copy(io.Discard, resp.Body)
				_ = resp.Body.Close()
				if resp.StatusCode < 200 || resp.StatusCode > 299 {
					result.non2xx++
				}
				result.latencies = append(result.latencies, float64(time.Since(started).Microseconds())/1000)
			}
			resultCh <- result
		}()
	}
	results := make([]workerResult, 0, size)
	for worker := 0; worker < size; worker++ {
		results = append(results, <-resultCh)
	}
	return results
}

// percentile expects values sorted ascending.
func percentile(values []float64, p float64) float64 {
	if len(values) == 0 {
		return 0
	}
	index := int(math.Ceil((p/100)*float64(len(values)))) - 1
	if index < 0 {
		index = 0
	}
	if index >= len(values) {
		index = len(values) - 1
	}
	return round2(values[index])
}

func round2(value float64) float64 {
	return math.Round(value*100) / 100
}
