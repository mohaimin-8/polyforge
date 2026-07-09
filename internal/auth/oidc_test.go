package auth_test

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"polyforge/internal/auth"
)

// fakeIdP is a minimal Keycloak stand-in: discovery, JWKS, and a token
// endpoint that enforces PKCE — it recomputes S256(code_verifier) and
// compares it to the challenge captured at the authorization step.
type fakeIdP struct {
	server    *httptest.Server
	key       *rsa.PrivateKey
	kid       string
	clientID  string
	code      string
	challenge string
}

func newFakeIdP(t *testing.T) *fakeIdP {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	idp := &fakeIdP{key: key, kid: "test-key-1", clientID: "polyforge-web", code: "auth-code-42"}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /.well-known/openid-configuration", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{
			"issuer":                 idp.server.URL,
			"authorization_endpoint": idp.server.URL + "/authorize",
			"token_endpoint":         idp.server.URL + "/token",
			"jwks_uri":               idp.server.URL + "/jwks",
		})
	})
	mux.HandleFunc("GET /jwks", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"keys": []map[string]string{{
				"kty": "RSA", "kid": idp.kid, "alg": "RS256", "use": "sig",
				"n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()),
				"e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes()),
			}},
		})
	})
	// The browser redirect: record the challenge, hand back the code.
	mux.HandleFunc("GET /authorize", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("code_challenge_method") != "S256" {
			http.Error(w, "plain PKCE rejected", http.StatusBadRequest)
			return
		}
		idp.challenge = r.URL.Query().Get("code_challenge")
		_ = json.NewEncoder(w).Encode(map[string]string{"code": idp.code})
	})
	mux.HandleFunc("POST /token", func(w http.ResponseWriter, r *http.Request) {
		if err := r.ParseForm(); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if r.Form.Get("code") != idp.code {
			http.Error(w, "unknown code", http.StatusBadRequest)
			return
		}
		sum := sha256.Sum256([]byte(r.Form.Get("code_verifier")))
		if base64.RawURLEncoding.EncodeToString(sum[:]) != idp.challenge {
			http.Error(w, "PKCE verification failed", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "at-1",
			"token_type":   "Bearer",
			"expires_in":   300,
			"id_token": idp.signToken(t, map[string]any{
				"iss": idp.server.URL, "sub": "user-7", "aud": idp.clientID,
				"exp": time.Now().Add(5 * time.Minute).Unix(), "iat": time.Now().Unix(),
				"nonce": "nonce-1", "preferred_username": "mohaiminul",
			}),
		})
	})
	idp.server = httptest.NewServer(mux)
	t.Cleanup(idp.server.Close)
	return idp
}

func (idp *fakeIdP) signToken(t *testing.T, claims map[string]any) string {
	t.Helper()
	header, _ := json.Marshal(map[string]string{"alg": "RS256", "typ": "JWT", "kid": idp.kid})
	payload, _ := json.Marshal(claims)
	signingInput := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(payload)
	digest := sha256.Sum256([]byte(signingInput))
	signature, err := rsa.SignPKCS1v15(rand.Reader, idp.key, crypto.SHA256, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return signingInput + "." + base64.RawURLEncoding.EncodeToString(signature)
}

// TestOIDCCodePKCEFlowEndToEnd is the W23 verification gate: authorize →
// code → PKCE-checked exchange → ID token validated against the IdP JWKS.
func TestOIDCCodePKCEFlowEndToEnd(t *testing.T) {
	idp := newFakeIdP(t)
	provider := auth.NewOIDCProvider(idp.server.URL, idp.clientID)

	pkce, err := auth.NewPKCE()
	if err != nil {
		t.Fatal(err)
	}
	authURL, err := provider.AuthCodeURL(t.Context(), "https://polyforge.example/callback", "state-1", "nonce-1", pkce)
	if err != nil {
		t.Fatal(err)
	}

	// "Browser" step: hit the authorization endpoint, get the code.
	resp, err := http.Get(authURL)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("authorize status = %d", resp.StatusCode)
	}
	var authResult struct{ Code string }
	if err := json.NewDecoder(resp.Body).Decode(&authResult); err != nil {
		t.Fatal(err)
	}

	tokens, err := provider.Exchange(t.Context(), authResult.Code, "https://polyforge.example/callback", pkce)
	if err != nil {
		t.Fatalf("exchange: %v", err)
	}
	claims, err := provider.VerifyIDToken(t.Context(), tokens.IDToken)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if claims.Subject != "user-7" || claims.Nonce != "nonce-1" || claims.PreferredUsername != "mohaiminul" {
		t.Fatalf("claims = %+v", claims)
	}
}

func TestOIDCExchangeFailsWithWrongVerifier(t *testing.T) {
	idp := newFakeIdP(t)
	provider := auth.NewOIDCProvider(idp.server.URL, idp.clientID)

	good, _ := auth.NewPKCE()
	if _, err := provider.AuthCodeURL(t.Context(), "https://cb", "s", "n", good); err != nil {
		t.Fatal(err)
	}
	if _, err := http.Get(idp.server.URL + "/authorize?code_challenge=" + url.QueryEscape(good.Challenge) + "&code_challenge_method=S256"); err != nil {
		t.Fatal(err)
	}

	stolen, _ := auth.NewPKCE() // attacker has the code but not the verifier
	if _, err := provider.Exchange(t.Context(), idp.code, "https://cb", stolen); err == nil {
		t.Fatal("exchange with the wrong verifier must fail")
	}
}

func TestVerifyIDTokenRejectsBadTokens(t *testing.T) {
	idp := newFakeIdP(t)
	provider := auth.NewOIDCProvider(idp.server.URL, idp.clientID)
	now := time.Now()

	valid := map[string]any{
		"iss": idp.server.URL, "sub": "u", "aud": idp.clientID,
		"exp": now.Add(time.Hour).Unix(), "iat": now.Unix(),
	}

	t.Run("wrong audience", func(t *testing.T) {
		claims := map[string]any{}
		for k, v := range valid {
			claims[k] = v
		}
		claims["aud"] = "other-client"
		if _, err := provider.VerifyIDToken(t.Context(), idp.signToken(t, claims)); !errors.Is(err, auth.ErrOIDCTokenInvalid) {
			t.Fatalf("err = %v, want ErrOIDCTokenInvalid", err)
		}
	})

	t.Run("expired", func(t *testing.T) {
		claims := map[string]any{}
		for k, v := range valid {
			claims[k] = v
		}
		claims["exp"] = now.Add(-time.Hour).Unix()
		if _, err := provider.VerifyIDToken(t.Context(), idp.signToken(t, claims)); !errors.Is(err, auth.ErrOIDCTokenInvalid) {
			t.Fatalf("err = %v, want ErrOIDCTokenInvalid", err)
		}
	})

	t.Run("wrong issuer", func(t *testing.T) {
		claims := map[string]any{}
		for k, v := range valid {
			claims[k] = v
		}
		claims["iss"] = "https://evil.example"
		if _, err := provider.VerifyIDToken(t.Context(), idp.signToken(t, claims)); !errors.Is(err, auth.ErrOIDCTokenInvalid) {
			t.Fatalf("err = %v, want ErrOIDCTokenInvalid", err)
		}
	})

	t.Run("tampered payload", func(t *testing.T) {
		token := idp.signToken(t, valid)
		parts := strings.Split(token, ".")
		forged, _ := json.Marshal(map[string]any{
			"iss": idp.server.URL, "sub": "admin", "aud": idp.clientID,
			"exp": now.Add(time.Hour).Unix(), "iat": now.Unix(),
		})
		parts[1] = base64.RawURLEncoding.EncodeToString(forged)
		if _, err := provider.VerifyIDToken(t.Context(), strings.Join(parts, ".")); !errors.Is(err, auth.ErrOIDCTokenInvalid) {
			t.Fatalf("err = %v, want ErrOIDCTokenInvalid", err)
		}
	})

	t.Run("audience array is accepted", func(t *testing.T) {
		claims := map[string]any{}
		for k, v := range valid {
			claims[k] = v
		}
		claims["aud"] = []string{"other", idp.clientID}
		if _, err := provider.VerifyIDToken(t.Context(), idp.signToken(t, claims)); err != nil {
			t.Fatalf("array aud rejected: %v", err)
		}
	})
}
