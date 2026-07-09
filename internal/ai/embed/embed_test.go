package embed_test

import (
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"testing"

	"polyforge/internal/ai/embed"
)

func TestLocalEmbedderIsDeterministicAndNormalized(t *testing.T) {
	e := embed.NewLocal(256)
	vecs, err := e.Embed(t.Context(), []string{"How do I sort an array?", "How do I sort an array?"})
	if err != nil {
		t.Fatalf("embed: %v", err)
	}
	if got := embed.Cosine(vecs[0], vecs[1]); math.Abs(got-1) > 1e-6 {
		t.Fatalf("identical texts have cosine %f, want 1", got)
	}
	var norm float64
	for _, v := range vecs[0] {
		norm += float64(v) * float64(v)
	}
	if math.Abs(norm-1) > 1e-5 {
		t.Fatalf("vector norm^2 = %f, want 1", norm)
	}
}

func TestLocalEmbedderRanksSimilarTextCloser(t *testing.T) {
	e := embed.NewLocal(256)
	vecs, err := e.Embed(t.Context(), []string{
		"how do i sort an array in python",
		"how do i sort an array in python quickly",
		"the weather in tokyo is rainy today",
	})
	if err != nil {
		t.Fatalf("embed: %v", err)
	}
	similar := embed.Cosine(vecs[0], vecs[1])
	dissimilar := embed.Cosine(vecs[0], vecs[2])
	if similar <= dissimilar {
		t.Fatalf("similar=%f should exceed dissimilar=%f", similar, dissimilar)
	}
	if similar < 0.8 {
		t.Fatalf("near-duplicate similarity %f is too low to drive a semantic cache", similar)
	}
}

func TestOpenAIEmbedderSpeaksTheWireProtocol(t *testing.T) {
	var sawAuth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sawAuth = r.Header.Get("Authorization")
		if r.URL.Path != "/v1/embeddings" {
			t.Errorf("path = %s, want /v1/embeddings", r.URL.Path)
		}
		var req struct {
			Model string   `json:"model"`
			Input []string `json:"input"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		// Answer out of order on purpose: the client must reassemble by index.
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []map[string]any{
				{"index": 1, "embedding": []float32{0, 1, 0}},
				{"index": 0, "embedding": []float32{3, 0, 4}},
			},
		})
	}))
	defer server.Close()

	e := embed.NewOpenAI(server.URL+"/v1", "sk-test", "text-embedding-3-small", 3)
	vecs, err := e.Embed(t.Context(), []string{"first", "second"})
	if err != nil {
		t.Fatalf("embed: %v", err)
	}
	if sawAuth != "Bearer sk-test" {
		t.Fatalf("Authorization = %q", sawAuth)
	}
	// [3,0,4] normalizes to [0.6, 0, 0.8].
	if math.Abs(float64(vecs[0][0])-0.6) > 1e-6 || math.Abs(float64(vecs[0][2])-0.8) > 1e-6 {
		t.Fatalf("vector 0 not normalized/reordered correctly: %v", vecs[0])
	}
	if vecs[1][1] != 1 {
		t.Fatalf("vector 1 misassigned: %v", vecs[1])
	}
}

func TestOpenAIEmbedderSurfacesAPIErrors(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, `{"error":"rate limited"}`, http.StatusTooManyRequests)
	}))
	defer server.Close()

	e := embed.NewOpenAI(server.URL, "", "m", 3)
	if _, err := e.Embed(t.Context(), []string{"x"}); err == nil {
		t.Fatal("expected an error from a 429 response")
	}
}
