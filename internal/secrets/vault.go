package secrets

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// Vault reads one KV-v2 secret object and serves its keys as named
// secrets. Two auth paths: a static token (dev mode), or the Kubernetes
// auth method, where the pod logs in with its ServiceAccount token and
// receives a short-lived Vault token (roadmap W23).
type Vault struct {
	addr  string
	mount string
	path  string

	client *http.Client

	mu          sync.Mutex
	token       string
	tokenExpiry time.Time
	k8sRole     string
	k8sJWTPath  string
}

func NewVault(addr, token, mount, path string) *Vault {
	return &Vault{
		addr:   strings.TrimRight(addr, "/"),
		token:  token,
		mount:  mount,
		path:   path,
		client: &http.Client{Timeout: 10 * time.Second},
	}
}

// UseKubernetesAuth switches from the static token to ServiceAccount
// login. The Vault token is cached and renewed before its lease expires.
func (v *Vault) UseKubernetesAuth(role, jwtPath string) {
	v.mu.Lock()
	defer v.mu.Unlock()
	v.k8sRole = role
	v.k8sJWTPath = jwtPath
	v.token = ""
}

func (v *Vault) currentToken(ctx context.Context) (string, error) {
	v.mu.Lock()
	defer v.mu.Unlock()
	if v.k8sRole == "" {
		if v.token == "" {
			return "", errors.New("vault: no token and no kubernetes auth configured")
		}
		return v.token, nil
	}
	if v.token != "" && time.Now().Before(v.tokenExpiry) {
		return v.token, nil
	}
	jwt, err := os.ReadFile(v.k8sJWTPath)
	if err != nil {
		return "", fmt.Errorf("vault: read serviceaccount token: %w", err)
	}
	body, err := json.Marshal(map[string]string{
		"role": v.k8sRole,
		"jwt":  strings.TrimSpace(string(jwt)),
	})
	if err != nil {
		return "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		v.addr+"/v1/auth/kubernetes/login", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	resp, err := v.client.Do(req)
	if err != nil {
		return "", err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return "", fmt.Errorf("vault: kubernetes login returned %d: %s", resp.StatusCode, detail)
	}
	var parsed struct {
		Auth struct {
			ClientToken   string `json:"client_token"`
			LeaseDuration int    `json:"lease_duration"`
		} `json:"auth"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return "", err
	}
	if parsed.Auth.ClientToken == "" {
		return "", errors.New("vault: login response had no client_token")
	}
	v.token = parsed.Auth.ClientToken
	// Renew at 2/3 of the lease so a request never rides an expired token.
	v.tokenExpiry = time.Now().Add(time.Duration(parsed.Auth.LeaseDuration) * time.Second * 2 / 3)
	return v.token, nil
}

func (v *Vault) Secret(ctx context.Context, name string) (string, error) {
	token, err := v.currentToken(ctx)
	if err != nil {
		return "", err
	}
	url := fmt.Sprintf("%s/v1/%s/data/%s", v.addr, v.mount, v.path)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("X-Vault-Token", token)
	resp, err := v.client.Do(req)
	if err != nil {
		return "", err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode == http.StatusNotFound {
		return "", fmt.Errorf("vault path %s: %w", v.path, ErrNotFound)
	}
	if resp.StatusCode != http.StatusOK {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return "", fmt.Errorf("vault returned %d: %s", resp.StatusCode, detail)
	}
	var parsed struct {
		Data struct {
			Data map[string]string `json:"data"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return "", err
	}
	value, ok := parsed.Data.Data[name]
	if !ok || value == "" {
		return "", fmt.Errorf("vault key %s: %w", name, ErrNotFound)
	}
	return value, nil
}
