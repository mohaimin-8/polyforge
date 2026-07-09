package gateway

import (
	"encoding/json"
	"fmt"
	"os"
)

// BackendSpec is the on-disk form of one backend in the routing config.
// API keys are referenced by environment variable name, never stored in the
// file, so the config can live in git.
type BackendSpec struct {
	Name string `json:"name"`
	// Kind selects the adapter: "ollama" (native API) or "openai"
	// (OpenAI-compatible: OpenAI, Groq, vLLM, Ollama's /v1 shim).
	Kind            string  `json:"kind"`
	BaseURL         string  `json:"base_url"`
	Model           string  `json:"model"`
	APIKeyEnv       string  `json:"api_key_env,omitempty"`
	CostPer1MTokens float64 `json:"cost_per_1m_tokens,omitempty"`
}

// RouterFile is the declarative routing policy the gateway loads at boot
// (roadmap W20): backends plus the RouterConfig rule table.
type RouterFile struct {
	Backends []BackendSpec `json:"backends"`
	RouterConfig
}

func LoadRouterFile(path string) (RouterFile, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return RouterFile{}, err
	}
	var file RouterFile
	if err := json.Unmarshal(raw, &file); err != nil {
		return RouterFile{}, fmt.Errorf("parse routing config %s: %w", path, err)
	}
	return file, nil
}

// Build constructs the live providers and the Router from the file.
func (f RouterFile) Build() (*Router, error) {
	backends := make([]Backend, 0, len(f.Backends))
	for _, spec := range f.Backends {
		provider, err := spec.Provider()
		if err != nil {
			return nil, err
		}
		backends = append(backends, Backend{
			Name:            spec.Name,
			Provider:        provider,
			CostPer1MTokens: spec.CostPer1MTokens,
		})
	}
	return NewRouter(f.RouterConfig, backends...)
}

// Provider constructs the adapter a spec describes.
func (spec BackendSpec) Provider() (Provider, error) {
	if spec.BaseURL == "" {
		return nil, fmt.Errorf("backend %q needs a base_url", spec.Name)
	}
	switch spec.Kind {
	case "ollama":
		return NewOllama(spec.BaseURL, spec.Model), nil
	case "openai", "":
		return NewOpenAICompat(spec.BaseURL, os.Getenv(spec.APIKeyEnv), spec.Model), nil
	default:
		return nil, fmt.Errorf("backend %q has unknown kind %q", spec.Name, spec.Kind)
	}
}
