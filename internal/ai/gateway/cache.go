package gateway

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"sync/atomic"

	"polyforge/internal/ai/embed"
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
}

func NewSemanticCache(embedder embed.Embedder, threshold float64) *SemanticCache {
	if threshold <= 0 || threshold > 1 {
		threshold = DefaultCacheThreshold
	}
	return &SemanticCache{
		embedder:  embedder,
		index:     vector.NewIndex(embedder.Dimensions()),
		threshold: threshold,
	}
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
	matches := c.index.Search(tenantID, vecs[0], 1)
	if len(matches) == 0 || matches[0].Score < c.threshold {
		c.misses.Add(1)
		return "", 0, false
	}
	c.hits.Add(1)
	return matches[0].Doc.Meta["completion"], matches[0].Score, true
}

// Store records a completed exchange for future lookups.
func (c *SemanticCache) Store(ctx context.Context, tenantID string, messages []Message, completion string) {
	text := cacheKeyText(messages)
	vecs, err := c.embedder.Embed(ctx, []string{text})
	if err != nil {
		return
	}
	sum := sha256.Sum256([]byte(text))
	_ = c.index.Upsert(vector.Doc{
		ID:       hex.EncodeToString(sum[:16]),
		TenantID: tenantID,
		Text:     text,
		Vector:   vecs[0],
		Meta:     map[string]string{"completion": completion},
	})
}

// Stats reports lifetime hit/miss counters for metrics.
func (c *SemanticCache) Stats() (hits, misses int64) {
	return c.hits.Load(), c.misses.Load()
}
