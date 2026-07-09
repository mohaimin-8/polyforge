// Package embed abstracts embedding generation behind one interface with
// two providers: a remote OpenAI-compatible API and a deterministic local
// hashed n-gram embedder. The local embedder is not a language model — it
// captures lexical, not semantic, similarity — but it is deterministic,
// offline, and fast, which makes the semantic-cache and vector-search
// machinery fully testable without network access or model weights
// (the same no-Docker verification stance as ADR 0005/0006).
package embed

import (
	"context"
	"math"
)

type Embedder interface {
	// Embed returns one L2-normalized vector per input text.
	Embed(ctx context.Context, texts []string) ([][]float32, error)
	Dimensions() int
	Model() string
}

// Local is the deterministic hashed character-n-gram embedder.
type Local struct {
	dims int
}

func NewLocal(dims int) *Local {
	if dims <= 0 {
		dims = 256
	}
	return &Local{dims: dims}
}

func (l *Local) Dimensions() int { return l.dims }
func (l *Local) Model() string   { return "local-ngram-hash" }

func (l *Local) Embed(_ context.Context, texts []string) ([][]float32, error) {
	out := make([][]float32, len(texts))
	for i, text := range texts {
		out[i] = l.embedOne(text)
	}
	return out, nil
}

func (l *Local) embedOne(text string) []float32 {
	vec := make([]float32, l.dims)
	runes := []rune(normalize(text))
	// Character trigrams weighted into hashed buckets. Shared trigrams
	// between two texts land in the same buckets, so lexically similar
	// texts get high cosine similarity.
	for n := 2; n <= 4; n++ {
		for i := 0; i+n <= len(runes); i++ {
			h := fnv32(runes[i : i+n])
			bucket := int(h % uint32(l.dims))
			// A second hash decides the sign, which keeps the expected dot
			// product of unrelated texts near zero (feature hashing).
			if (h>>16)&1 == 1 {
				vec[bucket]++
			} else {
				vec[bucket]--
			}
		}
	}
	return Normalize(vec)
}

func normalize(text string) string {
	out := make([]rune, 0, len(text))
	lastSpace := false
	for _, r := range text {
		switch {
		case r >= 'A' && r <= 'Z':
			out = append(out, r+('a'-'A'))
			lastSpace = false
		case r == ' ' || r == '\t' || r == '\n' || r == '\r':
			if !lastSpace {
				out = append(out, ' ')
			}
			lastSpace = true
		default:
			out = append(out, r)
			lastSpace = false
		}
	}
	return string(out)
}

func fnv32(runes []rune) uint32 {
	const prime = 16777619
	hash := uint32(2166136261)
	for _, r := range runes {
		hash ^= uint32(r)
		hash *= prime
	}
	return hash
}

// Normalize scales a vector to unit length, making cosine similarity a
// plain dot product downstream.
func Normalize(vec []float32) []float32 {
	var sum float64
	for _, v := range vec {
		sum += float64(v) * float64(v)
	}
	if sum == 0 {
		return vec
	}
	norm := float32(1 / math.Sqrt(sum))
	for i := range vec {
		vec[i] *= norm
	}
	return vec
}

// Cosine returns the cosine similarity of two same-length vectors. For
// normalized vectors this is the dot product.
func Cosine(a, b []float32) float64 {
	var dot float64
	for i := range a {
		dot += float64(a[i]) * float64(b[i])
	}
	return dot
}
