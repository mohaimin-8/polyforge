package gateway

import (
	"context"
	"sync"
	"sync/atomic"
)

// CanaryProvider splits traffic between a stable and a canary Provider
// (roadmap W22). In-mesh the same split runs at the Linkerd TrafficSplit
// layer (deploy/linkerd/); this application-level twin exists so the
// rollback behavior is testable without a cluster and works when the
// canary is a different model host rather than a different pod version.
//
// Rollback is automatic: once the canary's error rate over its last
// windowSize requests crosses tripRatio (with a minimum sample), the
// breaker trips and all traffic shifts back to stable until Reset.
type CanaryProvider struct {
	stable Provider
	canary Provider
	// weight is the percentage of requests (0-100) sent to the canary.
	weight int

	position atomic.Int64 // deterministic traffic position, mod 100

	mu      sync.Mutex
	window  []bool // ring buffer: true = the canary request failed
	next    int
	filled  int
	tripped bool
}

const (
	canaryWindowSize = 20
	canaryMinSample  = 5
	canaryTripRatio  = 0.5
)

func NewCanaryProvider(stable, canary Provider, weightPercent int) *CanaryProvider {
	if weightPercent < 0 {
		weightPercent = 0
	}
	if weightPercent > 100 {
		weightPercent = 100
	}
	return &CanaryProvider{
		stable: stable,
		canary: canary,
		weight: weightPercent,
		window: make([]bool, canaryWindowSize),
	}
}

// pick routes deterministically by traffic position rather than randomly:
// exactly weight% of every 100 requests hit the canary, which makes the
// split assertable and the rollout gradual even at low traffic.
func (c *CanaryProvider) pick() Provider {
	if c.weight == 0 || c.Tripped() {
		return c.stable
	}
	if int(c.position.Add(1)-1)%100 < c.weight {
		return c.canary
	}
	return c.stable
}

func (c *CanaryProvider) observe(failed bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.tripped {
		return
	}
	c.window[c.next] = failed
	c.next = (c.next + 1) % len(c.window)
	if c.filled < len(c.window) {
		c.filled++
	}
	if c.filled < canaryMinSample {
		return
	}
	failures := 0
	for i := 0; i < c.filled; i++ {
		if c.window[i] {
			failures++
		}
	}
	if float64(failures)/float64(c.filled) >= canaryTripRatio {
		c.tripped = true
	}
}

// Tripped reports whether the breaker has rolled traffic back to stable.
func (c *CanaryProvider) Tripped() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.tripped
}

// Reset re-arms the canary after an operator has shipped a fix.
func (c *CanaryProvider) Reset() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.tripped = false
	c.window = make([]bool, canaryWindowSize)
	c.next, c.filled = 0, 0
}

func (c *CanaryProvider) Chat(ctx context.Context, req ChatRequest) (ChatResponse, error) {
	provider := c.pick()
	response, err := provider.Chat(ctx, req)
	if provider == c.canary {
		c.observe(err != nil)
	}
	return response, err
}

func (c *CanaryProvider) StreamChat(ctx context.Context, req ChatRequest, onDelta func(string) error) (ChatResponse, error) {
	provider := c.pick()
	response, err := provider.StreamChat(ctx, req, onDelta)
	if provider == c.canary {
		c.observe(err != nil)
	}
	return response, err
}
