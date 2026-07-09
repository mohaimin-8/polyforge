package embed

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// OpenAI calls any OpenAI-compatible /v1/embeddings endpoint (OpenAI
// itself, Ollama, TEI serving BGE, etc.). The provider choice is
// configuration, not code — the platform's abstraction requirement.
type OpenAI struct {
	baseURL string
	apiKey  string
	model   string
	dims    int
	client  *http.Client
}

func NewOpenAI(baseURL, apiKey, model string, dims int) *OpenAI {
	if dims <= 0 {
		dims = 1536 // text-embedding-3-small
	}
	return &OpenAI{
		baseURL: strings.TrimRight(baseURL, "/"),
		apiKey:  apiKey,
		model:   model,
		dims:    dims,
		client:  &http.Client{Timeout: 30 * time.Second},
	}
}

func (o *OpenAI) Dimensions() int { return o.dims }
func (o *OpenAI) Model() string   { return o.model }

func (o *OpenAI) Embed(ctx context.Context, texts []string) ([][]float32, error) {
	body, err := json.Marshal(map[string]any{"model": o.model, "input": texts})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, o.baseURL+"/embeddings", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if o.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+o.apiKey)
	}
	resp, err := o.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("embeddings API returned %d: %s", resp.StatusCode, detail)
	}
	var parsed struct {
		Data []struct {
			Index     int       `json:"index"`
			Embedding []float32 `json:"embedding"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return nil, err
	}
	if len(parsed.Data) != len(texts) {
		return nil, fmt.Errorf("embeddings API returned %d vectors for %d inputs", len(parsed.Data), len(texts))
	}
	out := make([][]float32, len(texts))
	for _, item := range parsed.Data {
		if item.Index < 0 || item.Index >= len(out) {
			return nil, fmt.Errorf("embeddings API returned out-of-range index %d", item.Index)
		}
		out[item.Index] = Normalize(item.Embedding)
	}
	return out, nil
}
