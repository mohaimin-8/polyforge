package secrets_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"polyforge/internal/secrets"
)

func TestEnvSource(t *testing.T) {
	t.Setenv("POLYFORGE_TEST_SECRET", "from-env")
	value, err := secrets.Env{}.Secret(t.Context(), "POLYFORGE_TEST_SECRET")
	if err != nil || value != "from-env" {
		t.Fatalf("env secret = %q/%v", value, err)
	}
	if _, err := (secrets.Env{}).Secret(t.Context(), "POLYFORGE_TEST_MISSING"); !errors.Is(err, secrets.ErrNotFound) {
		t.Fatalf("missing env err = %v, want ErrNotFound", err)
	}
}

func TestDirSourceRereadsOnRotation(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "DB_PASSWORD")
	if err := os.WriteFile(path, []byte("hunter2\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	source := secrets.Dir{Path: dir}

	value, err := source.Secret(t.Context(), "DB_PASSWORD")
	if err != nil || value != "hunter2" {
		t.Fatalf("secret = %q/%v, want hunter2 (trimmed)", value, err)
	}

	// A sidecar rewriting the file rotates the credential with no restart:
	// the W23 gate is pickup within 60s; re-reading per call makes it immediate.
	if err := os.WriteFile(path, []byte("hunter3"), 0o600); err != nil {
		t.Fatal(err)
	}
	value, err = source.Secret(t.Context(), "DB_PASSWORD")
	if err != nil || value != "hunter3" {
		t.Fatalf("rotated secret = %q/%v, want hunter3", value, err)
	}

	if _, err := source.Secret(t.Context(), "ABSENT"); !errors.Is(err, secrets.ErrNotFound) {
		t.Fatalf("absent file err = %v, want ErrNotFound", err)
	}
}

func TestChainPrefersEarlierSources(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "SHARED"), []byte("from-file"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("SHARED", "from-env")
	t.Setenv("ENV_ONLY", "env-value")

	chain := secrets.Chain{secrets.Dir{Path: dir}, secrets.Env{}}
	if value, _ := chain.Secret(t.Context(), "SHARED"); value != "from-file" {
		t.Fatalf("SHARED = %q, want the file layer to win", value)
	}
	if value, _ := chain.Secret(t.Context(), "ENV_ONLY"); value != "env-value" {
		t.Fatalf("ENV_ONLY = %q, want fallthrough to env", value)
	}
	if _, err := chain.Secret(t.Context(), "NOWHERE"); !errors.Is(err, secrets.ErrNotFound) {
		t.Fatalf("NOWHERE err = %v, want ErrNotFound", err)
	}
}

// fakeVault implements enough of the KV-v2 + Kubernetes-auth API to prove
// the client: SA login issues a token, reads require it.
func fakeVault(t *testing.T, kv map[string]string) *httptest.Server {
	t.Helper()
	const issued = "vault-token-123"
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/auth/kubernetes/login":
			var body struct{ Role, JWT string }
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.Role != "polyforge" || body.JWT == "" {
				http.Error(w, "bad login", http.StatusForbidden)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"auth": map[string]any{"client_token": issued, "lease_duration": 3600},
			})
		case "/v1/secret/data/polyforge":
			if r.Header.Get("X-Vault-Token") != issued {
				http.Error(w, "permission denied", http.StatusForbidden)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"data": map[string]any{"data": kv},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(ts.Close)
	return ts
}

func TestVaultKubernetesAuthAndKVRead(t *testing.T) {
	server := fakeVault(t, map[string]string{"POLYFORGE_ADMIN_KEY": "vault-admin-key"})

	jwtPath := filepath.Join(t.TempDir(), "sa-token")
	if err := os.WriteFile(jwtPath, []byte("sa-jwt"), 0o600); err != nil {
		t.Fatal(err)
	}
	vault := secrets.NewVault(server.URL, "", "secret", "polyforge")
	vault.UseKubernetesAuth("polyforge", jwtPath)

	value, err := vault.Secret(context.Background(), "POLYFORGE_ADMIN_KEY")
	if err != nil {
		t.Fatalf("vault secret: %v", err)
	}
	if value != "vault-admin-key" {
		t.Fatalf("value = %q", value)
	}
	if _, err := vault.Secret(context.Background(), "ABSENT_KEY"); !errors.Is(err, secrets.ErrNotFound) {
		t.Fatalf("absent key err = %v, want ErrNotFound", err)
	}
}

func TestVaultStaticTokenRejectedWhenWrong(t *testing.T) {
	server := fakeVault(t, map[string]string{"K": "v"})
	vault := secrets.NewVault(server.URL, "wrong-token", "secret", "polyforge")
	if _, err := vault.Secret(context.Background(), "K"); err == nil {
		t.Fatal("expected an error for a rejected token")
	}
}
