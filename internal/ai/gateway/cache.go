package gateway

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/gateway/eviction"
	"polyforge/internal/ai/vector"
)

// DefaultCacheThreshold is deliberately high: the cache must only fire on
// near-duplicate prompts. Serving a similar-but-different question a stale
// answer is a correctness bug, not a cache win.
const DefaultCacheThreshold = 0.95

// SemanticCache answers repeated (or near-duplicate) prompts from history
// instead of the model. Entries are tenant-scoped: one tenant's prompts
// must never warm another tenant's cache — that would leak information
// across the isolation boundary.
type SemanticCache struct {
	embedder  embed.Embedder
	index     *vector.Index
	threshold float64
	hits      atomic.Int64
	misses    atomic.Int64

	// W28: with a per-tenant capacity set, the cost-aware policy bounds
	// the cache; unbounded remains the default for compatibility.
	capacityPerTenant int
	policyMu          sync.Mutex
	policies          map[string]eviction.Policy

	// Live cache knob (PREREG_WAVE4_LIVE_PLANE.md §Substrate 3): per-tenant
	// byte budgets set at runtime by the operator's Policy. Presence in
	// budgets makes the partition bounded; 0 disables admission entirely.
	// All three maps are guarded by policyMu.
	budgets    map[string]int64
	usedBytes  map[string]int64
	entrySizes map[string]map[string]int64

	// shared collapses every tenant into one cache partition — the
	// deliberately INSECURE posture, off by default. It exists only so the
	// cross-tenant timing side channel this design prevents can be measured
	// as a baseline (research/security/PREREG_WIRE_ATTACK.md); a production
	// gateway must never set it. When false (the default) the cache is
	// per-tenant and isolation is unconditional.
	shared bool
}

// sharedPartition is the single key every tenant collapses onto in the
// insecure shared posture.
const sharedPartition = "__shared__"

// WithShared enables the insecure single-partition cache posture. Off by
// default; see the `shared` field.
func (c *SemanticCache) WithShared(shared bool) *SemanticCache {
	c.shared = shared
	return c
}

// partition is the cache key namespace for a tenant: the tenant's own id
// (isolated, the default and only safe posture) or one shared bucket.
func (c *SemanticCache) partition(tenantID string) string {
	if c.shared {
		return sharedPartition
	}
	return tenantID
}

func NewSemanticCache(embedder embed.Embedder, threshold float64) *SemanticCache {
	if threshold <= 0 || threshold > 1 {
		threshold = DefaultCacheThreshold
	}
	return &SemanticCache{
		embedder:   embedder,
		index:      vector.NewIndex(embedder.Dimensions()),
		threshold:  threshold,
		policies:   map[string]eviction.Policy{},
		budgets:    map[string]int64{},
		usedBytes:  map[string]int64{},
		entrySizes: map[string]map[string]int64{},
	}
}

// WithCapacity bounds the cache to n entries per tenant, evicted by the
// W28 cost-aware policy (internal/ai/gateway/eviction). Per-tenant, not
// global: a noisy tenant evicting a quiet tenant's expensive entries
// would be a cross-tenant interference channel.
func (c *SemanticCache) WithCapacity(n int) *SemanticCache {
	c.capacityPerTenant = n
	return c
}

func (c *SemanticCache) policyFor(tenantID string) eviction.Policy {
	policy, ok := c.policies[tenantID]
	if !ok {
		policy = eviction.NewCostAware(eviction.CostAwareConfig{})
		c.policies[tenantID] = policy
	}
	return policy
}

// cacheKeyText flattens the conversation so the whole context, not just the
// last user turn, must match for a hit.
func cacheKeyText(messages []Message) string {
	var b strings.Builder
	for _, m := range messages {
		b.WriteString(m.Role)
		b.WriteString(": ")
		b.WriteString(m.Content)
		b.WriteString("\n")
	}
	return b.String()
}

// Lookup returns a cached completion when a stored prompt is similar enough.
func (c *SemanticCache) Lookup(ctx context.Context, tenantID string, messages []Message) (string, float64, bool) {
	vecs, err := c.embedder.Embed(ctx, []string{cacheKeyText(messages)})
	if err != nil {
		// A broken embedder must never break chat: fail open as a miss.
		c.misses.Add(1)
		return "", 0, false
	}
	part := c.partition(tenantID)
	matches := c.index.Search(part, vecs[0], 1)
	if len(matches) == 0 || matches[0].Score < c.threshold {
		c.misses.Add(1)
		return "", 0, false
	}
	c.hits.Add(1)
	// Policy metadata is maintained unconditionally so a byte budget set
	// *after* entries were stored can still evict them in preference order.
	c.policyMu.Lock()
	c.policyFor(part).OnHit(matches[0].Doc.ID, time.Now())
	c.policyMu.Unlock()
	return matches[0].Doc.Meta["completion"], matches[0].Score, true
}

// Store records a completed exchange for future lookups, estimating the
// recompute cost from the completion length at the local-tier price.
func (c *SemanticCache) Store(ctx context.Context, tenantID string, messages []Message, completion string) {
	// docs/INFERENCE_BENCH.md local tier: ~$0.0004 per 1k tokens, ~4
	// chars per token. Callers that know the real backend cost should
	// use StoreWithCost.
	estimate := float64(len(completion)) / 4 / 1000 * 0.0004
	c.StoreWithCost(ctx, tenantID, messages, completion, estimate)
}

// StoreWithCost records a completed exchange with the caller's true
// recompute cost, which the W28 eviction policy weighs directly.
func (c *SemanticCache) StoreWithCost(ctx context.Context, tenantID string, messages []Message, completion string, costUSD float64) {
	part := c.partition(tenantID)
	c.policyMu.Lock()
	if budget, ok := c.budgets[part]; ok && budget == 0 {
		// A zero budget disables the tenant's cache: no admission at all,
		// so the miss path stays honest (nothing warms invisibly).
		c.policyMu.Unlock()
		return
	}
	c.policyMu.Unlock()
	text := cacheKeyText(messages)
	vecs, err := c.embedder.Embed(ctx, []string{text})
	if err != nil {
		return
	}
	// Semantic density: similarity to the nearest already-cached prompt.
	// Measured before the insert so the entry never counts itself.
	density := 0.0
	if nearest := c.index.Search(part, vecs[0], 1); len(nearest) > 0 && nearest[0].Score > 0 {
		density = nearest[0].Score
	}
	sum := sha256.Sum256([]byte(text))
	id := hex.EncodeToString(sum[:16])
	if err := c.index.Upsert(vector.Doc{
		ID:       id,
		TenantID: part,
		Text:     text,
		Vector:   vecs[0],
		Meta:     map[string]string{"completion": completion},
	}); err != nil {
		return
	}
	now := time.Now()
	c.policyMu.Lock()
	defer c.policyMu.Unlock()
	policy := c.policyFor(part)
	policy.OnStore(eviction.Entry{
		ID:               id,
		SizeBytes:        int64(len(completion)),
		RecomputeCostUSD: costUSD,
		EmbeddingDensity: density,
		StoredAt:         now,
	}, now)
	c.accountStoreLocked(part, id, int64(len(completion)))
	c.evictWhileOverLocked(part, now)
}

// accountStoreLocked updates the byte accounting for one admission,
// handling the Upsert-replaces-by-ID case. policyMu must be held.
func (c *SemanticCache) accountStoreLocked(part, id string, size int64) {
	sizes, ok := c.entrySizes[part]
	if !ok {
		sizes = map[string]int64{}
		c.entrySizes[part] = sizes
	}
	if old, ok := sizes[id]; ok {
		c.usedBytes[part] -= old
	}
	sizes[id] = size
	c.usedBytes[part] += size
}

func (c *SemanticCache) removeAccountingLocked(part, id string) {
	if sizes, ok := c.entrySizes[part]; ok {
		if size, ok := sizes[id]; ok {
			c.usedBytes[part] -= size
			delete(sizes, id)
		}
	}
}

// overBoundLocked reports whether the partition exceeds either bound: the
// process-wide entry capacity (W28) or the partition's byte budget (the
// live cache knob). policyMu must be held.
func (c *SemanticCache) overBoundLocked(part string) bool {
	if c.capacityPerTenant > 0 && c.index.Count(part) > c.capacityPerTenant {
		return true
	}
	if budget, ok := c.budgets[part]; ok && c.usedBytes[part] > budget {
		return true
	}
	return false
}

func (c *SemanticCache) evictWhileOverLocked(part string, now time.Time) {
	policy := c.policyFor(part)
	for c.overBoundLocked(part) {
		victim, ok := policy.Victim(now)
		if !ok {
			break
		}
		c.index.Delete(part, victim)
		policy.OnRemove(victim)
		c.removeAccountingLocked(part, victim)
	}
}

// SetTenantBudgetBytes sets (or resets) the tenant's cache byte budget and
// evicts down to it immediately. A negative budget removes the bound.
func (c *SemanticCache) SetTenantBudgetBytes(tenantID string, budget int64) {
	part := c.partition(tenantID)
	c.policyMu.Lock()
	defer c.policyMu.Unlock()
	if budget < 0 {
		delete(c.budgets, part)
		return
	}
	c.budgets[part] = budget
	c.evictWhileOverLocked(part, time.Now())
}

// UsedBytes reports the tenant's current cache footprint (completion bytes).
func (c *SemanticCache) UsedBytes(tenantID string) int64 {
	part := c.partition(tenantID)
	c.policyMu.Lock()
	defer c.policyMu.Unlock()
	return c.usedBytes[part]
}

// Stats reports lifetime hit/miss counters for metrics.
func (c *SemanticCache) Stats() (hits, misses int64) {
	return c.hits.Load(), c.misses.Load()
}
