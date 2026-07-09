// Package secrets resolves named credentials from wherever the deployment
// keeps them (roadmap W23). The chain is: files injected by a Vault Agent
// sidecar (or K8s Secret mount), then Vault's HTTP API, then process env.
// Application code asks for "POLYFORGE_ADMIN_KEY" and stays ignorant of
// which layer answered — rotating a secret in Vault needs no restart when
// the agent rewrites the file.
package secrets

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// ErrNotFound reports that a source does not hold the named secret; a
// Chain treats it as "try the next source", any other error as fatal.
var ErrNotFound = errors.New("secret not found")

type Source interface {
	// Secret returns the value for name, or ErrNotFound.
	Secret(ctx context.Context, name string) (string, error)
}

// Env resolves secrets from environment variables — the pre-W23 behavior,
// kept as the last link of every chain so local development needs nothing.
type Env struct{}

func (Env) Secret(_ context.Context, name string) (string, error) {
	if value, ok := os.LookupEnv(name); ok && value != "" {
		return value, nil
	}
	return "", fmt.Errorf("env %s: %w", name, ErrNotFound)
}

// Dir resolves each secret from a file named after it in one directory.
// This is the shape both Vault Agent template output and mounted K8s
// Secrets take. Values are re-read per call, so a sidecar rewriting the
// file rotates the credential without a process restart.
type Dir struct{ Path string }

func (d Dir) Secret(_ context.Context, name string) (string, error) {
	raw, err := os.ReadFile(filepath.Join(d.Path, name))
	if errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("file %s: %w", name, ErrNotFound)
	}
	if err != nil {
		return "", err
	}
	value := strings.TrimSpace(string(raw))
	if value == "" {
		return "", fmt.Errorf("file %s is empty: %w", name, ErrNotFound)
	}
	return value, nil
}

// Chain asks each source in order and returns the first hit.
type Chain []Source

func (c Chain) Secret(ctx context.Context, name string) (string, error) {
	for _, source := range c {
		value, err := source.Secret(ctx, name)
		if err == nil {
			return value, nil
		}
		if !errors.Is(err, ErrNotFound) {
			return "", err
		}
	}
	return "", fmt.Errorf("%s: %w in any source", name, ErrNotFound)
}

// FromEnvironment assembles the deployment's chain from its own env:
// POLYFORGE_SECRETS_DIR mounts a file directory ahead of the environment,
// POLYFORGE_VAULT_ADDR puts Vault's KV engine ahead of both.
func FromEnvironment() Source {
	var chain Chain
	if dir := os.Getenv("POLYFORGE_SECRETS_DIR"); dir != "" {
		chain = append(chain, Dir{Path: dir})
	}
	if addr := os.Getenv("POLYFORGE_VAULT_ADDR"); addr != "" {
		vault := NewVault(addr, os.Getenv("POLYFORGE_VAULT_TOKEN"),
			envOr("POLYFORGE_VAULT_MOUNT", "secret"),
			envOr("POLYFORGE_VAULT_PATH", "polyforge"))
		if role := os.Getenv("POLYFORGE_VAULT_K8S_ROLE"); role != "" {
			vault.UseKubernetesAuth(role,
				envOr("POLYFORGE_VAULT_SA_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token"))
		}
		chain = append(chain, vault)
	}
	return append(chain, Env{})
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
