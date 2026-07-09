// Package vector implements the tenant-scoped in-memory vector index behind
// semantic search and the semantic cache. Search is exact (brute-force
// cosine over normalized vectors); the production-scale path is pgvector
// with an HNSW index, which trades exactness for sub-linear search — this
// implementation is the recall=1.0 baseline that variant is benchmarked
// against.
package vector

import (
	"errors"
	"sort"
	"sync"

	"polyforge/internal/ai/embed"
)

type Doc struct {
	ID       string            `json:"id"`
	TenantID string            `json:"tenant_id"`
	Text     string            `json:"text"`
	Vector   []float32         `json:"-"`
	Meta     map[string]string `json:"meta,omitempty"`
}

type Match struct {
	Doc   Doc     `json:"doc"`
	Score float64 `json:"score"`
}

type Index struct {
	mu   sync.RWMutex
	dims int
	docs map[string]map[string]Doc // tenantID -> docID -> doc
}

func NewIndex(dims int) *Index {
	return &Index{dims: dims, docs: map[string]map[string]Doc{}}
}

func (x *Index) Upsert(doc Doc) error {
	if doc.TenantID == "" || doc.ID == "" {
		return errors.New("doc requires tenant_id and id")
	}
	if len(doc.Vector) != x.dims {
		return errors.New("vector dimensionality does not match the index")
	}
	x.mu.Lock()
	defer x.mu.Unlock()
	tenantDocs := x.docs[doc.TenantID]
	if tenantDocs == nil {
		tenantDocs = map[string]Doc{}
		x.docs[doc.TenantID] = tenantDocs
	}
	tenantDocs[doc.ID] = doc
	return nil
}

func (x *Index) Delete(tenantID, id string) {
	x.mu.Lock()
	defer x.mu.Unlock()
	delete(x.docs[tenantID], id)
}

// Count reports how many documents a tenant has indexed.
func (x *Index) Count(tenantID string) int {
	x.mu.RLock()
	defer x.mu.RUnlock()
	return len(x.docs[tenantID])
}

// Search returns the tenant's top-k documents by cosine similarity.
// Isolation is structural: the scan never touches another tenant's map, so
// there is no filter to get wrong.
func (x *Index) Search(tenantID string, query []float32, k int) []Match {
	if k <= 0 {
		k = 10
	}
	x.mu.RLock()
	defer x.mu.RUnlock()
	tenantDocs := x.docs[tenantID]
	matches := make([]Match, 0, len(tenantDocs))
	for _, doc := range tenantDocs {
		matches = append(matches, Match{Doc: doc, Score: embed.Cosine(query, doc.Vector)})
	}
	sort.Slice(matches, func(i, j int) bool {
		if matches[i].Score != matches[j].Score {
			return matches[i].Score > matches[j].Score
		}
		return matches[i].Doc.ID < matches[j].Doc.ID
	})
	if len(matches) > k {
		matches = matches[:k]
	}
	return matches
}
