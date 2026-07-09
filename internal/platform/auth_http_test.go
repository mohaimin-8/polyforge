package platform

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"polyforge/internal/auth"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

func newAuthTestServer(t *testing.T) (*httptest.Server, tenant.APIKey, tenant.APIKey) {
	t.Helper()
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	telemetryStore := telemetry.NewStore(100)
	_, _ = tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "alpha", Name: "Alpha"})
	readKey, _ := tenantStore.CreateAPIKey(ctx, "alpha", "reader", tenant.ScopeRead)
	fullKey, _ := tenantStore.CreateAPIKey(ctx, "alpha", "writer", tenant.ScopeFull)
	server := httptest.NewServer(NewServer(slog.New(slog.NewTextHandler(io.Discard, nil)), tenantStore, telemetryStore, Config{AdminKey: "admin-test"}).Handler())
	t.Cleanup(server.Close)
	return server, readKey, fullKey
}

func requestToken(t *testing.T, server *httptest.Server, apiKey string) (auth.TokenPair, int) {
	t.Helper()
	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/auth/token", bytes.NewBufferString(`{"tenant_id":"alpha"}`))
	req.Header.Set("Content-Type", "application/json")
	if apiKey != "" {
		req.Header.Set("X-PolyForge-API-Key", apiKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	var pair auth.TokenPair
	_ = json.NewDecoder(resp.Body).Decode(&pair)
	return pair, resp.StatusCode
}

func doBearer(t *testing.T, method, url, token, body string) int {
	t.Helper()
	var reader io.Reader
	if body != "" {
		reader = bytes.NewBufferString(body)
	}
	req, _ := http.NewRequest(method, url, reader)
	req.Header.Set("Authorization", "Bearer "+token)
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	return resp.StatusCode
}

func TestAuthTokenIssuanceAndBearerRBAC(t *testing.T) {
	server, readKey, fullKey := newAuthTestServer(t)

	t.Run("missing api key is 401", func(t *testing.T) {
		if _, status := requestToken(t, server, ""); status != http.StatusUnauthorized {
			t.Fatalf("expected 401, got %d", status)
		}
	})

	t.Run("bad api key is 401", func(t *testing.T) {
		if _, status := requestToken(t, server, "pf_not_a_real_key"); status != http.StatusUnauthorized {
			t.Fatalf("expected 401, got %d", status)
		}
	})

	t.Run("full-scope token can write", func(t *testing.T) {
		pair, status := requestToken(t, server, fullKey.Secret)
		if status != http.StatusOK || pair.AccessToken == "" || pair.RefreshToken == "" || pair.TokenType != "Bearer" {
			t.Fatalf("unexpected token response: status=%d pair=%+v", status, pair)
		}
		status = doBearer(t, http.MethodPost, server.URL+"/v1/tenants/alpha/projects", pair.AccessToken, `{"id":"proj-1","name":"Project One"}`)
		if status != http.StatusCreated {
			t.Fatalf("expected 201, got %d", status)
		}
	})

	// The W7 verification gate, restated for JWTs: a read-scope credential
	// must get 403 (not 401) on a mutating endpoint.
	t.Run("read-scope token gets 403 on write and 200 on read", func(t *testing.T) {
		pair, status := requestToken(t, server, readKey.Secret)
		if status != http.StatusOK {
			t.Fatalf("expected 200, got %d", status)
		}
		if status := doBearer(t, http.MethodGet, server.URL+"/v1/tenants/alpha/projects", pair.AccessToken, ""); status != http.StatusOK {
			t.Fatalf("expected 200 on read, got %d", status)
		}
		if status := doBearer(t, http.MethodPost, server.URL+"/v1/tenants/alpha/projects", pair.AccessToken, `{"id":"proj-2","name":"Project Two"}`); status != http.StatusForbidden {
			t.Fatalf("expected 403 on write, got %d", status)
		}
	})

	t.Run("token for another tenant path is 403", func(t *testing.T) {
		pair, _ := requestToken(t, server, fullKey.Secret)
		if status := doBearer(t, http.MethodGet, server.URL+"/v1/tenants/bravo/projects", pair.AccessToken, ""); status != http.StatusForbidden {
			t.Fatalf("expected 403, got %d", status)
		}
	})

	t.Run("garbage bearer token is 401", func(t *testing.T) {
		if status := doBearer(t, http.MethodGet, server.URL+"/v1/tenants/alpha/projects", "not.a.jwt", ""); status != http.StatusUnauthorized {
			t.Fatalf("expected 401, got %d", status)
		}
	})
}

func TestRefreshEndpointRotatesAndRejectsReplay(t *testing.T) {
	server, _, fullKey := newAuthTestServer(t)
	pair, _ := requestToken(t, server, fullKey.Secret)

	refresh := func(token string) (auth.TokenPair, int) {
		body, _ := json.Marshal(map[string]string{"refresh_token": token})
		resp, err := http.Post(server.URL+"/v1/auth/token/refresh", "application/json", bytes.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = resp.Body.Close() }()
		var rotated auth.TokenPair
		_ = json.NewDecoder(resp.Body).Decode(&rotated)
		return rotated, resp.StatusCode
	}

	rotated, status := refresh(pair.RefreshToken)
	if status != http.StatusOK || rotated.AccessToken == "" || rotated.RefreshToken == pair.RefreshToken {
		t.Fatalf("unexpected refresh response: status=%d pair=%+v", status, rotated)
	}
	if status := doBearer(t, http.MethodGet, server.URL+"/v1/tenants/alpha/projects", rotated.AccessToken, ""); status != http.StatusOK {
		t.Fatalf("refreshed access token rejected: %d", status)
	}
	if _, status := refresh(pair.RefreshToken); status != http.StatusUnauthorized {
		t.Fatalf("expected 401 on replayed refresh token, got %d", status)
	}
}

func TestJWKSAndKeyRotationEndpoints(t *testing.T) {
	server, _, fullKey := newAuthTestServer(t)
	pair, _ := requestToken(t, server, fullKey.Secret)

	fetchJWKS := func() (map[string]any, int) {
		resp, err := http.Get(server.URL + "/.well-known/jwks.json")
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = resp.Body.Close() }()
		var doc map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&doc)
		return doc, resp.StatusCode
	}

	doc, status := fetchJWKS()
	if status != http.StatusOK {
		t.Fatalf("expected 200 from JWKS, got %d", status)
	}
	if keys, ok := doc["keys"].([]any); !ok || len(keys) != 1 {
		t.Fatalf("expected one published key before rotation, got %v", doc)
	}

	t.Run("rotation requires admin key", func(t *testing.T) {
		resp, err := http.Post(server.URL+"/v1/auth/keys/rotate", "application/json", nil)
		if err != nil {
			t.Fatal(err)
		}
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusUnauthorized {
			t.Fatalf("expected 401, got %d", resp.StatusCode)
		}
	})

	t.Run("rotation keeps issued tokens valid without restart", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/auth/keys/rotate", nil)
		req.Header.Set("X-PolyForge-Admin-Key", "admin-test")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("expected 200, got %d", resp.StatusCode)
		}

		doc, _ := fetchJWKS()
		if keys, ok := doc["keys"].([]any); !ok || len(keys) != 2 {
			t.Fatalf("expected two published keys after rotation, got %v", doc)
		}
		// The same live server keeps accepting the pre-rotation token.
		if status := doBearer(t, http.MethodGet, server.URL+"/v1/tenants/alpha/projects", pair.AccessToken, ""); status != http.StatusOK {
			t.Fatalf("pre-rotation token rejected after rotation: %d", status)
		}
	})
}
