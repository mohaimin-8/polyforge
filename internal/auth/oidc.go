package auth

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// OIDC relying-party support (roadmap W23): PolyForge federates login to
// an external IdP (Keycloak in the reference deployment) using the
// authorization-code flow with PKCE (RFC 7636), then validates the ID
// token against the IdP's JWKS. Stdlib-only, mirroring ADR 0004: the
// protocol surface used is small enough that a dependency would mostly
// import parsing code we already wrote for the issuer side.

var (
	ErrOIDCTokenInvalid = errors.New("oidc token invalid")
)

const oidcClockSkew = 2 * time.Minute

// OIDCProvider is one configured IdP relationship.
type OIDCProvider struct {
	issuer   string
	clientID string
	client   *http.Client

	mu        sync.Mutex
	discovery *oidcDiscovery
	keys      map[string]*rsa.PublicKey
}

func NewOIDCProvider(issuer, clientID string) *OIDCProvider {
	return &OIDCProvider{
		issuer:   strings.TrimRight(issuer, "/"),
		clientID: clientID,
		client:   &http.Client{Timeout: 10 * time.Second},
		keys:     map[string]*rsa.PublicKey{},
	}
}

type oidcDiscovery struct {
	Issuer                string `json:"issuer"`
	AuthorizationEndpoint string `json:"authorization_endpoint"`
	TokenEndpoint         string `json:"token_endpoint"`
	JWKSURI               string `json:"jwks_uri"`
}

func (p *OIDCProvider) discover(ctx context.Context) (oidcDiscovery, error) {
	p.mu.Lock()
	cached := p.discovery
	p.mu.Unlock()
	if cached != nil {
		return *cached, nil
	}
	var disco oidcDiscovery
	if err := p.getJSON(ctx, p.issuer+"/.well-known/openid-configuration", &disco); err != nil {
		return oidcDiscovery{}, fmt.Errorf("oidc discovery: %w", err)
	}
	p.mu.Lock()
	p.discovery = &disco
	p.mu.Unlock()
	return disco, nil
}

func (p *OIDCProvider) getJSON(ctx context.Context, endpoint string, dst any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s returned %d", endpoint, resp.StatusCode)
	}
	return json.NewDecoder(resp.Body).Decode(dst)
}

// keyFor returns the IdP's public key for kid, refetching the JWKS once on
// a miss so key rotation at the IdP needs no PolyForge restart.
func (p *OIDCProvider) keyFor(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	p.mu.Lock()
	key, ok := p.keys[kid]
	p.mu.Unlock()
	if ok {
		return key, nil
	}
	disco, err := p.discover(ctx)
	if err != nil {
		return nil, err
	}
	var jwks struct {
		Keys []struct {
			Kid string `json:"kid"`
			Kty string `json:"kty"`
			N   string `json:"n"`
			E   string `json:"e"`
		} `json:"keys"`
	}
	if err := p.getJSON(ctx, disco.JWKSURI, &jwks); err != nil {
		return nil, fmt.Errorf("fetch jwks: %w", err)
	}
	fresh := map[string]*rsa.PublicKey{}
	for _, k := range jwks.Keys {
		if k.Kty != "RSA" {
			continue
		}
		n, err := base64.RawURLEncoding.DecodeString(k.N)
		if err != nil {
			continue
		}
		e, err := base64.RawURLEncoding.DecodeString(k.E)
		if err != nil {
			continue
		}
		fresh[k.Kid] = &rsa.PublicKey{
			N: new(big.Int).SetBytes(n),
			E: int(new(big.Int).SetBytes(e).Int64()),
		}
	}
	p.mu.Lock()
	p.keys = fresh
	key, ok = p.keys[kid]
	p.mu.Unlock()
	if !ok {
		return nil, fmt.Errorf("%w: no jwks key with kid %q", ErrOIDCTokenInvalid, kid)
	}
	return key, nil
}

// PKCE is one login attempt's verifier/challenge pair (RFC 7636, S256).
// The verifier stays server-side with the session; only the challenge
// travels through the browser.
type PKCE struct {
	Verifier  string
	Challenge string
}

func NewPKCE() (PKCE, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return PKCE{}, err
	}
	verifier := base64.RawURLEncoding.EncodeToString(raw)
	sum := sha256.Sum256([]byte(verifier))
	return PKCE{
		Verifier:  verifier,
		Challenge: base64.RawURLEncoding.EncodeToString(sum[:]),
	}, nil
}

// AuthCodeURL builds the IdP redirect that starts the code+PKCE flow.
func (p *OIDCProvider) AuthCodeURL(ctx context.Context, redirectURI, state, nonce string, pkce PKCE) (string, error) {
	disco, err := p.discover(ctx)
	if err != nil {
		return "", err
	}
	query := url.Values{
		"response_type":         {"code"},
		"client_id":             {p.clientID},
		"redirect_uri":          {redirectURI},
		"scope":                 {"openid profile email"},
		"state":                 {state},
		"nonce":                 {nonce},
		"code_challenge":        {pkce.Challenge},
		"code_challenge_method": {"S256"},
	}
	return disco.AuthorizationEndpoint + "?" + query.Encode(), nil
}

// TokenResponse is the IdP's answer to the code exchange.
type TokenResponse struct {
	AccessToken  string `json:"access_token"`
	IDToken      string `json:"id_token"`
	RefreshToken string `json:"refresh_token"`
	TokenType    string `json:"token_type"`
	ExpiresIn    int    `json:"expires_in"`
}

// Exchange trades the authorization code (plus the PKCE verifier) for
// tokens at the IdP's token endpoint.
func (p *OIDCProvider) Exchange(ctx context.Context, code, redirectURI string, pkce PKCE) (TokenResponse, error) {
	disco, err := p.discover(ctx)
	if err != nil {
		return TokenResponse{}, err
	}
	form := url.Values{
		"grant_type":    {"authorization_code"},
		"code":          {code},
		"redirect_uri":  {redirectURI},
		"client_id":     {p.clientID},
		"code_verifier": {pkce.Verifier},
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, disco.TokenEndpoint,
		strings.NewReader(form.Encode()))
	if err != nil {
		return TokenResponse{}, err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := p.client.Do(req)
	if err != nil {
		return TokenResponse{}, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return TokenResponse{}, fmt.Errorf("token endpoint returned %d: %s", resp.StatusCode, detail)
	}
	var tokens TokenResponse
	if err := json.NewDecoder(resp.Body).Decode(&tokens); err != nil {
		return TokenResponse{}, err
	}
	if tokens.IDToken == "" {
		return TokenResponse{}, fmt.Errorf("%w: token response had no id_token", ErrOIDCTokenInvalid)
	}
	return tokens, nil
}

// audience tolerates the spec's string-or-array encoding of aud.
type audience []string

func (a *audience) UnmarshalJSON(raw []byte) error {
	if len(raw) > 0 && raw[0] == '"' {
		var single string
		if err := json.Unmarshal(raw, &single); err != nil {
			return err
		}
		*a = audience{single}
		return nil
	}
	var many []string
	if err := json.Unmarshal(raw, &many); err != nil {
		return err
	}
	*a = audience(many)
	return nil
}

// IDClaims are the ID-token claims PolyForge consumes.
type IDClaims struct {
	Issuer            string   `json:"iss"`
	Subject           string   `json:"sub"`
	Audience          audience `json:"aud"`
	Expiry            int64    `json:"exp"`
	IssuedAt          int64    `json:"iat"`
	Nonce             string   `json:"nonce"`
	Email             string   `json:"email"`
	PreferredUsername string   `json:"preferred_username"`
}

// VerifyIDToken checks the RS256 signature against the IdP's JWKS and
// validates iss, aud, exp, and iat (with bounded clock skew). The caller
// compares Nonce against the login session's nonce.
func (p *OIDCProvider) VerifyIDToken(ctx context.Context, raw string) (IDClaims, error) {
	parts := strings.Split(raw, ".")
	if len(parts) != 3 {
		return IDClaims{}, fmt.Errorf("%w: not a compact JWT", ErrOIDCTokenInvalid)
	}
	headerJSON, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return IDClaims{}, fmt.Errorf("%w: bad header encoding", ErrOIDCTokenInvalid)
	}
	var header struct {
		Alg string `json:"alg"`
		Kid string `json:"kid"`
	}
	if err := json.Unmarshal(headerJSON, &header); err != nil {
		return IDClaims{}, fmt.Errorf("%w: bad header", ErrOIDCTokenInvalid)
	}
	// Pinning the algorithm defeats both alg=none and RS/HS confusion.
	if header.Alg != "RS256" {
		return IDClaims{}, fmt.Errorf("%w: alg %q is not RS256", ErrOIDCTokenInvalid, header.Alg)
	}
	key, err := p.keyFor(ctx, header.Kid)
	if err != nil {
		return IDClaims{}, err
	}
	signature, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return IDClaims{}, fmt.Errorf("%w: bad signature encoding", ErrOIDCTokenInvalid)
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], signature); err != nil {
		return IDClaims{}, fmt.Errorf("%w: signature mismatch", ErrOIDCTokenInvalid)
	}

	payloadJSON, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return IDClaims{}, fmt.Errorf("%w: bad payload encoding", ErrOIDCTokenInvalid)
	}
	var claims IDClaims
	if err := json.Unmarshal(payloadJSON, &claims); err != nil {
		return IDClaims{}, fmt.Errorf("%w: bad claims", ErrOIDCTokenInvalid)
	}
	if claims.Issuer != p.issuer {
		return IDClaims{}, fmt.Errorf("%w: issuer %q is not %q", ErrOIDCTokenInvalid, claims.Issuer, p.issuer)
	}
	audOK := false
	for _, aud := range claims.Audience {
		if aud == p.clientID {
			audOK = true
		}
	}
	if !audOK {
		return IDClaims{}, fmt.Errorf("%w: audience does not include %q", ErrOIDCTokenInvalid, p.clientID)
	}
	now := time.Now()
	if claims.Expiry > 0 && now.After(time.Unix(claims.Expiry, 0).Add(oidcClockSkew)) {
		return IDClaims{}, fmt.Errorf("%w: expired", ErrOIDCTokenInvalid)
	}
	if claims.IssuedAt > 0 && now.Add(oidcClockSkew).Before(time.Unix(claims.IssuedAt, 0)) {
		return IDClaims{}, fmt.Errorf("%w: issued in the future", ErrOIDCTokenInvalid)
	}
	return claims, nil
}
