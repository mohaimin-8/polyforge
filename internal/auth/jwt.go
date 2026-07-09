// Package auth implements the W7 token layer: an RS256 JWT issuer with a
// JWKS endpoint and single-use refresh-token rotation. It is stdlib-only,
// matching the project's dependency discipline: JWS compact serialization is
// small enough that a library would mostly add attack surface.
//
// Refresh tokens are held in memory (hashed), so they do not survive a
// process restart. That is an accepted limitation until the auth service is
// split out (see docs/adr/0004-stdlib-jwt-issuer.md); access tokens keep
// working across restarts of *other* services because verification only
// needs the public key from JWKS.
package auth

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
)

const (
	// DefaultAccessTTL keeps stolen bearer tokens short-lived; clients are
	// expected to refresh.
	DefaultAccessTTL = 15 * time.Minute
	// DefaultRefreshTTL bounds how long a session can be extended without
	// re-presenting the API key.
	DefaultRefreshTTL = 24 * time.Hour
	// clockSkewLeeway tolerates small clock drift between issuer and verifier.
	clockSkewLeeway = 30 * time.Second

	tokenIssuer = "polyforge-control-plane"
)

var (
	ErrInvalidToken        = errors.New("invalid token")
	ErrTokenExpired        = errors.New("token expired")
	ErrInvalidRefreshToken = errors.New("invalid or already-used refresh token")
)

// Claims is the verified payload of a PolyForge access token.
type Claims struct {
	Issuer    string `json:"iss"`
	Subject   string `json:"sub"`
	TenantID  string `json:"tenant_id"`
	Scope     string `json:"scope"`
	IssuedAt  int64  `json:"iat"`
	ExpiresAt int64  `json:"exp"`
	ID        string `json:"jti"`
}

// TokenPair is what token issuance and refresh return, shaped like an OAuth2
// token response so clients need no PolyForge-specific handling.
type TokenPair struct {
	AccessToken  string `json:"access_token"`
	TokenType    string `json:"token_type"`
	ExpiresIn    int64  `json:"expires_in"`
	RefreshToken string `json:"refresh_token"`
}

type signingKey struct {
	kid string
	key *rsa.PrivateKey
}

type refreshRecord struct {
	tenantID  string
	scope     string
	expiresAt time.Time
}

// Issuer issues and verifies RS256 access tokens and rotates refresh tokens.
// Key rotation keeps the previous key verifiable so tokens signed before a
// rotation remain valid until they expire — rotation never requires a
// service restart or an immediate client re-auth.
type Issuer struct {
	mu       sync.RWMutex
	current  *signingKey
	previous *signingKey
	refresh  map[string]refreshRecord

	now        func() time.Time
	accessTTL  time.Duration
	refreshTTL time.Duration
	lastPrune  time.Time
}

// NewIssuer generates the initial RSA-2048 signing key. It only fails if the
// platform's entropy source is broken.
func NewIssuer() (*Issuer, error) {
	key, err := generateSigningKey()
	if err != nil {
		return nil, err
	}
	return &Issuer{
		current:    key,
		refresh:    make(map[string]refreshRecord),
		now:        time.Now,
		accessTTL:  DefaultAccessTTL,
		refreshTTL: DefaultRefreshTTL,
	}, nil
}

func generateSigningKey() (*signingKey, error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, fmt.Errorf("generate RSA signing key: %w", err)
	}
	sum := sha256.Sum256(key.PublicKey.N.Bytes())
	return &signingKey{kid: hex.EncodeToString(sum[:8]), key: key}, nil
}

// Issue creates an access/refresh token pair for an authenticated principal.
// Authentication happens before this call (API key or admin key); Issue only
// encodes the already-established identity and scope.
func (i *Issuer) Issue(tenantID, scope string) (TokenPair, error) {
	now := i.now().UTC()

	i.mu.Lock()
	defer i.mu.Unlock()

	access, err := i.signAccessTokenLocked(tenantID, scope, now)
	if err != nil {
		return TokenPair{}, err
	}
	refresh, err := i.issueRefreshTokenLocked(tenantID, scope, now)
	if err != nil {
		return TokenPair{}, err
	}
	return TokenPair{
		AccessToken:  access,
		TokenType:    "Bearer",
		ExpiresIn:    int64(i.accessTTL.Seconds()),
		RefreshToken: refresh,
	}, nil
}

// Refresh exchanges a refresh token for a new pair. Tokens are single-use:
// the presented token is consumed even though a new one is returned, so a
// replayed (stolen) refresh token fails instead of silently minting a second
// session.
func (i *Issuer) Refresh(token string) (TokenPair, error) {
	now := i.now().UTC()
	digest := refreshDigest(token)

	i.mu.Lock()
	defer i.mu.Unlock()

	i.pruneRefreshLocked(now)
	record, ok := i.refresh[digest]
	if !ok {
		return TokenPair{}, ErrInvalidRefreshToken
	}
	delete(i.refresh, digest)
	if now.After(record.expiresAt) {
		return TokenPair{}, ErrInvalidRefreshToken
	}

	access, err := i.signAccessTokenLocked(record.tenantID, record.scope, now)
	if err != nil {
		return TokenPair{}, err
	}
	refresh, err := i.issueRefreshTokenLocked(record.tenantID, record.scope, now)
	if err != nil {
		return TokenPair{}, err
	}
	return TokenPair{
		AccessToken:  access,
		TokenType:    "Bearer",
		ExpiresIn:    int64(i.accessTTL.Seconds()),
		RefreshToken: refresh,
	}, nil
}

// Verify checks the signature (against the current or previous key, selected
// by kid), issuer, and expiry, and returns the claims.
func (i *Issuer) Verify(token string) (Claims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return Claims{}, ErrInvalidToken
	}
	headerJSON, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return Claims{}, ErrInvalidToken
	}
	var header struct {
		Alg string `json:"alg"`
		Kid string `json:"kid"`
	}
	if err := json.Unmarshal(headerJSON, &header); err != nil || header.Alg != "RS256" {
		return Claims{}, ErrInvalidToken
	}

	i.mu.RLock()
	key := i.publicKeyForLocked(header.Kid)
	i.mu.RUnlock()
	if key == nil {
		return Claims{}, ErrInvalidToken
	}

	signature, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return Claims{}, ErrInvalidToken
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], signature); err != nil {
		return Claims{}, ErrInvalidToken
	}

	payloadJSON, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return Claims{}, ErrInvalidToken
	}
	var claims Claims
	if err := json.Unmarshal(payloadJSON, &claims); err != nil {
		return Claims{}, ErrInvalidToken
	}
	if subtle.ConstantTimeCompare([]byte(claims.Issuer), []byte(tokenIssuer)) != 1 {
		return Claims{}, ErrInvalidToken
	}
	if i.now().UTC().After(time.Unix(claims.ExpiresAt, 0).Add(clockSkewLeeway)) {
		return Claims{}, ErrTokenExpired
	}
	return claims, nil
}

// Rotate generates a fresh signing key. The outgoing key is kept for
// verification only, so already-issued tokens stay valid; anything older
// than one rotation is dropped, which bounds the JWKS to two keys.
func (i *Issuer) Rotate() error {
	key, err := generateSigningKey()
	if err != nil {
		return err
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	i.previous = i.current
	i.current = key
	return nil
}

// JWKS returns the RFC 7517 key set for the current and, after a rotation,
// previous public keys.
func (i *Issuer) JWKS() map[string]any {
	i.mu.RLock()
	defer i.mu.RUnlock()
	keys := []map[string]string{jwk(i.current)}
	if i.previous != nil {
		keys = append(keys, jwk(i.previous))
	}
	return map[string]any{"keys": keys}
}

func jwk(k *signingKey) map[string]string {
	e := make([]byte, 8)
	binary.BigEndian.PutUint64(e, uint64(k.key.PublicKey.E))
	return map[string]string{
		"kty": "RSA",
		"use": "sig",
		"alg": "RS256",
		"kid": k.kid,
		"n":   base64.RawURLEncoding.EncodeToString(k.key.PublicKey.N.Bytes()),
		"e":   base64.RawURLEncoding.EncodeToString(trimLeadingZeros(e)),
	}
}

func trimLeadingZeros(value []byte) []byte {
	for len(value) > 1 && value[0] == 0 {
		value = value[1:]
	}
	return value
}

// PublicKeys exposes kid → public key, used by tests to prove JWKS material
// verifies real signatures.
func (i *Issuer) PublicKeys() map[string]*rsa.PublicKey {
	i.mu.RLock()
	defer i.mu.RUnlock()
	keys := map[string]*rsa.PublicKey{i.current.kid: &i.current.key.PublicKey}
	if i.previous != nil {
		keys[i.previous.kid] = &i.previous.key.PublicKey
	}
	return keys
}

func (i *Issuer) publicKeyForLocked(kid string) *rsa.PublicKey {
	if i.current != nil && i.current.kid == kid {
		return &i.current.key.PublicKey
	}
	if i.previous != nil && i.previous.kid == kid {
		return &i.previous.key.PublicKey
	}
	return nil
}

func (i *Issuer) signAccessTokenLocked(tenantID, scope string, now time.Time) (string, error) {
	jti := make([]byte, 16)
	if _, err := rand.Read(jti); err != nil {
		return "", fmt.Errorf("generate token id: %w", err)
	}
	claims := Claims{
		Issuer:    tokenIssuer,
		Subject:   "tenant:" + tenantID,
		TenantID:  tenantID,
		Scope:     scope,
		IssuedAt:  now.Unix(),
		ExpiresAt: now.Add(i.accessTTL).Unix(),
		ID:        hex.EncodeToString(jti),
	}
	headerJSON, err := json.Marshal(map[string]string{"alg": "RS256", "typ": "JWT", "kid": i.current.kid})
	if err != nil {
		return "", err
	}
	payloadJSON, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	signingInput := base64.RawURLEncoding.EncodeToString(headerJSON) + "." + base64.RawURLEncoding.EncodeToString(payloadJSON)
	digest := sha256.Sum256([]byte(signingInput))
	signature, err := rsa.SignPKCS1v15(rand.Reader, i.current.key, crypto.SHA256, digest[:])
	if err != nil {
		return "", fmt.Errorf("sign access token: %w", err)
	}
	return signingInput + "." + base64.RawURLEncoding.EncodeToString(signature), nil
}

func (i *Issuer) issueRefreshTokenLocked(tenantID, scope string, now time.Time) (string, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", fmt.Errorf("generate refresh token: %w", err)
	}
	token := "pfr_" + base64.RawURLEncoding.EncodeToString(raw)
	i.refresh[refreshDigest(token)] = refreshRecord{
		tenantID:  tenantID,
		scope:     scope,
		expiresAt: now.Add(i.refreshTTL),
	}
	return token, nil
}

// refreshDigest stores only a hash of the refresh token so a memory dump of
// the process does not leak usable credentials.
func refreshDigest(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func (i *Issuer) pruneRefreshLocked(now time.Time) {
	if now.Sub(i.lastPrune) < 10*time.Minute {
		return
	}
	i.lastPrune = now
	for digest, record := range i.refresh {
		if now.After(record.expiresAt) {
			delete(i.refresh, digest)
		}
	}
}
