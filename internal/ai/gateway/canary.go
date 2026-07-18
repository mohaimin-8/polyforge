package gateway

import (
	"context"
	"sync/atomic"

	"github.com/redis/go-redis/v9"
)

// CanaryProvider splits traffic between a stable and a canary Provider
// (roadmap W22). In-mesh the same split runs at the Linkerd TrafficSplit
// layer (deploy/linkerd/); this application-level twin exists so the
// rollback behavior is testable without a cluster and works when the
// canary is a different model host rather than a different pod version.
//
// Rollback is automatic: once the canary's error rate over its last
// windowSize requests crosses tripRatio (with a minimum sample), the
// breaker trips and all traffic shifts back to stable until Reset. With a
// Redis-backed breaker (NewSharedCanaryProvider) that decision is shared
// across gateway replicas, so a bad canary rolls back fleet-wide instead of
// each replica re-discovering the failure independently.
type CanaryProvider struct {
	stable Provider
	canary Provider
	// weight is the percentage of requests (0-100) sent to the canary.
	weight int

	position atomic.Int64 // deterministic traffic position, mod 100
	breaker  Breaker
}

func clampWeight(weightPercent int) int {
	if weightPercent < 0 {
		return 0
	}
	if weightPercent > 100 {
		return 100
	}
	return weightPercent
}

// NewCanaryProvider builds an in-process canary split: the breaker decision
// lives in this process only, which is correct for a single gateway replica.
func NewCanaryProvider(stable, canary Provider, weightPercent int) *CanaryProvider {
	return &CanaryProvider{
		stable:  stable,
		canary:  canary,
		weight:  clampWeight(weightPercent),
		breaker: newLocalBreaker(),
	}
}

// NewSharedCanaryProvider builds a canary split whose rollback breaker is
// shared across replicas through Redis, keyed by name (typically the canary
// deployment/model identity). Every replica observing the same key converges
// to the same trip decision.
func NewSharedCanaryProvider(stable, canary Provider, weightPercent int, client redis.UniversalClient, name string) *CanaryProvider {
	return &CanaryProvider{
		stable:  stable,
		canary:  canary,
		weight:  clampWeight(weightPercent),
		breaker: newRedisBreaker(client, name),
	}
}

// pick routes deterministically by traffic position rather than randomly:
// exactly weight% of every 100 requests hit the canary, which makes the
// split assertable and the rollout gradual even at low traffic.
func (c *CanaryProvider) pick(ctx context.Context) Provider {
	if c.weight == 0 || c.breaker.Tripped(ctx) {
		return c.stable
	}
	if int(c.position.Add(1)-1)%100 < c.weight {
		return c.canary
	}
	return c.stable
}

// Tripped reports whether the breaker has rolled traffic back to stable.
func (c *CanaryProvider) Tripped() bool {
	return c.breaker.Tripped(context.Background())
}

// Reset re-arms the canary after an operator has shipped a fix.
func (c *CanaryProvider) Reset() {
	c.breaker.Reset(context.Background())
}

func (c *CanaryProvider) Chat(ctx context.Context, req ChatRequest) (ChatResponse, error) {
	provider := c.pick(ctx)
	response, err := provider.Chat(ctx, req)
	if provider == c.canary {
		c.breaker.Observe(ctx, err != nil)
	}
	return response, err
}

func (c *CanaryProvider) StreamChat(ctx context.Context, req ChatRequest, onDelta func(string) error) (ChatResponse, error) {
	provider := c.pick(ctx)
	response, err := provider.StreamChat(ctx, req, onDelta)
	if provider == c.canary {
		c.breaker.Observe(ctx, err != nil)
	}
	return response, err
}
