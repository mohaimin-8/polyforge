package vector_test

import (
	"fmt"
	"math/rand"
	"testing"

	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/vector"
)

func TestSearchReturnsNearestFirst(t *testing.T) {
	e := embed.NewLocal(128)
	index := vector.NewIndex(128)
	corpus := []string{
		"sorting algorithm in python",
		"binary search tree implementation",
		"tokyo weather forecast",
	}
	for i, text := range corpus {
		vecs, _ := e.Embed(t.Context(), []string{text})
		if err := index.Upsert(vector.Doc{ID: fmt.Sprint(i), TenantID: "acme", Text: text, Vector: vecs[0]}); err != nil {
			t.Fatalf("upsert: %v", err)
		}
	}

	query, _ := e.Embed(t.Context(), []string{"how do I sort an array in python"})
	matches := index.Search("acme", query[0], 3)
	if len(matches) != 3 {
		t.Fatalf("got %d matches, want 3", len(matches))
	}
	if matches[0].Doc.Text != corpus[0] {
		t.Fatalf("top match = %q, want the sorting document", matches[0].Doc.Text)
	}
	for i := 1; i < len(matches); i++ {
		if matches[i].Score > matches[i-1].Score {
			t.Fatal("matches are not sorted by descending score")
		}
	}
}

func TestSearchIsTenantScoped(t *testing.T) {
	index := vector.NewIndex(2)
	_ = index.Upsert(vector.Doc{ID: "a", TenantID: "acme", Text: "secret", Vector: []float32{1, 0}})

	if matches := index.Search("globex", []float32{1, 0}, 10); len(matches) != 0 {
		t.Fatalf("tenant globex sees %d of acme's documents", len(matches))
	}
	if index.Count("acme") != 1 || index.Count("globex") != 0 {
		t.Fatal("per-tenant counts are wrong")
	}
}

func TestUpsertValidates(t *testing.T) {
	index := vector.NewIndex(2)
	if err := index.Upsert(vector.Doc{ID: "a", TenantID: "acme", Vector: []float32{1, 0, 0}}); err == nil {
		t.Fatal("dimension mismatch must be rejected")
	}
	if err := index.Upsert(vector.Doc{ID: "", TenantID: "acme", Vector: []float32{1, 0}}); err == nil {
		t.Fatal("missing ID must be rejected")
	}
}

func TestDeleteRemovesDoc(t *testing.T) {
	index := vector.NewIndex(2)
	_ = index.Upsert(vector.Doc{ID: "a", TenantID: "acme", Vector: []float32{1, 0}})
	index.Delete("acme", "a")
	if index.Count("acme") != 0 {
		t.Fatal("delete left the document behind")
	}
}

// BenchmarkSearch10k measures brute-force query cost at the roadmap's
// benchmark shape (top-10 over a large corpus).
func BenchmarkSearch10k(b *testing.B) {
	const dims = 256
	index := vector.NewIndex(dims)
	rng := rand.New(rand.NewSource(42))
	for i := 0; i < 10_000; i++ {
		vec := make([]float32, dims)
		for j := range vec {
			vec[j] = rng.Float32()*2 - 1
		}
		_ = index.Upsert(vector.Doc{ID: fmt.Sprint(i), TenantID: "bench", Vector: embed.Normalize(vec)})
	}
	query := make([]float32, dims)
	for j := range query {
		query[j] = rng.Float32()*2 - 1
	}
	query = embed.Normalize(query)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		index.Search("bench", query, 10)
	}
}
