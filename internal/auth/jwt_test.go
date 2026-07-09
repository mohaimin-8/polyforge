package auth

import (
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math/big"
	"strings"
	"testing"
	"time"
)

func newTestIssuer(t *testing.T) *Issuer {
	t.Helper()
	issuer, err := NewIssuer()
	if err != nil {
		t.Fatalf("NewIssuer: %v", err)
	}
	return issuer
}

func TestIssueAndVerifyRoundTrip(t *testing.T) {
	issuer := newTestIssuer(t)
	pair, err := issuer.Issue("acme", "read")
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	if pair.TokenType != "Bearer" || pair.ExpiresIn != int64(DefaultAccessTTL.Seconds()) {
		t.Fatalf("unexpected pair metadata: %+v", pair)
	}
	claims, err := issuer.Verify(pair.AccessToken)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if claims.TenantID != "acme" || claims.Scope != "read" || claims.Subject != "tenant:acme" {
		t.Fatalf("unexpected claims: %+v", claims)
	}
	if claims.ExpiresAt-claims.IssuedAt != int64(DefaultAccessTTL.Seconds()) {
		t.Fatalf("unexpected TTL: iat=%d exp=%d", claims.IssuedAt, claims.ExpiresAt)
	}
}

func TestVerifyRejectsExpiredToken(t *testing.T) {
	issuer := newTestIssuer(t)
	pair, err := issuer.Issue("acme", "full")
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	issuer.now = func() time.Time { return time.Now().Add(DefaultAccessTTL + time.Minute) }
	if _, err := issuer.Verify(pair.AccessToken); !errors.Is(err, ErrTokenExpired) {
		t.Fatalf("expected ErrTokenExpired, got %v", err)
	}
}

func TestVerifyRejectsTamperedToken(t *testing.T) {
	issuer := newTestIssuer(t)
	pair, err := issuer.Issue("acme", "read")
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	parts := strings.Split(pair.AccessToken, ".")

	// Swap the scope claim; the signature no longer matches.
	payload, _ := base64.RawURLEncoding.DecodeString(parts[1])
	tampered := strings.Replace(string(payload), `"scope":"read"`, `"scope":"full"`, 1)
	forged := parts[0] + "." + base64.RawURLEncoding.EncodeToString([]byte(tampered)) + "." + parts[2]
	if _, err := issuer.Verify(forged); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("expected ErrInvalidToken for tampered payload, got %v", err)
	}

	// A token signed by a different issuer's key must fail even if well-formed.
	other := newTestIssuer(t)
	otherPair, err := other.Issue("acme", "read")
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	if _, err := issuer.Verify(otherPair.AccessToken); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("expected ErrInvalidToken for foreign signature, got %v", err)
	}

	if _, err := issuer.Verify("not-a-jwt"); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("expected ErrInvalidToken for malformed token, got %v", err)
	}
}

func TestVerifyRejectsAlgorithmConfusion(t *testing.T) {
	issuer := newTestIssuer(t)
	pair, err := issuer.Issue("acme", "full")
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	parts := strings.Split(pair.AccessToken, ".")
	var header map[string]string
	raw, _ := base64.RawURLEncoding.DecodeString(parts[0])
	_ = json.Unmarshal(raw, &header)

	// alg=none downgrade: header rewritten, signature stripped.
	header["alg"] = "none"
	noneHeader, _ := json.Marshal(header)
	forged := base64.RawURLEncoding.EncodeToString(noneHeader) + "." + parts[1] + "."
	if _, err := issuer.Verify(forged); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("expected ErrInvalidToken for alg=none, got %v", err)
	}
}

func TestRefreshRotationIsSingleUse(t *testing.T) {
	issuer := newTestIssuer(t)
	pair, err := issuer.Issue("acme", "full")
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}

	rotated, err := issuer.Refresh(pair.RefreshToken)
	if err != nil {
		t.Fatalf("Refresh: %v", err)
	}
	claims, err := issuer.Verify(rotated.AccessToken)
	if err != nil {
		t.Fatalf("Verify refreshed token: %v", err)
	}
	if claims.TenantID != "acme" || claims.Scope != "full" {
		t.Fatalf("refresh did not preserve identity: %+v", claims)
	}
	if rotated.RefreshToken == pair.RefreshToken {
		t.Fatal("refresh must rotate the refresh token")
	}

	// Replaying the consumed token must fail — this is the rotation guarantee.
	if _, err := issuer.Refresh(pair.RefreshToken); !errors.Is(err, ErrInvalidRefreshToken) {
		t.Fatalf("expected ErrInvalidRefreshToken on replay, got %v", err)
	}
	// The new token still works.
	if _, err := issuer.Refresh(rotated.RefreshToken); err != nil {
		t.Fatalf("Refresh with rotated token: %v", err)
	}
}

func TestRefreshRejectsExpiredToken(t *testing.T) {
	issuer := newTestIssuer(t)
	pair, err := issuer.Issue("acme", "read")
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	issuer.now = func() time.Time { return time.Now().Add(DefaultRefreshTTL + time.Minute) }
	if _, err := issuer.Refresh(pair.RefreshToken); !errors.Is(err, ErrInvalidRefreshToken) {
		t.Fatalf("expected ErrInvalidRefreshToken for expired token, got %v", err)
	}
}

// TestKeyRotationWithoutRestart is the W7 verification gate: after Rotate,
// tokens signed by the old key still verify (previous key retained), new
// tokens carry the new kid, and the JWKS document publishes both keys with
// material that actually verifies real signatures.
func TestKeyRotationWithoutRestart(t *testing.T) {
	issuer := newTestIssuer(t)
	before, err := issuer.Issue("acme", "read")
	if err != nil {
		t.Fatalf("Issue: %v", err)
	}
	if err := issuer.Rotate(); err != nil {
		t.Fatalf("Rotate: %v", err)
	}
	if _, err := issuer.Verify(before.AccessToken); err != nil {
		t.Fatalf("pre-rotation token must stay valid: %v", err)
	}
	after, err := issuer.Issue("acme", "read")
	if err != nil {
		t.Fatalf("Issue after rotate: %v", err)
	}
	if _, err := issuer.Verify(after.AccessToken); err != nil {
		t.Fatalf("post-rotation token must verify: %v", err)
	}
	if kidOf(t, before.AccessToken) == kidOf(t, after.AccessToken) {
		t.Fatal("rotation must change the signing kid")
	}

	jwksKeys := issuer.JWKS()["keys"].([]map[string]string)
	if len(jwksKeys) != 2 {
		t.Fatalf("JWKS must publish current and previous keys, got %d", len(jwksKeys))
	}
	// Verify the post-rotation token using only JWKS material, the way an
	// external service (e.g. Postgres or a sidecar) would.
	verifyViaJWKS(t, jwksKeys, after.AccessToken)
	verifyViaJWKS(t, jwksKeys, before.AccessToken)
}

func kidOf(t *testing.T, token string) string {
	t.Helper()
	raw, err := base64.RawURLEncoding.DecodeString(strings.Split(token, ".")[0])
	if err != nil {
		t.Fatalf("decode header: %v", err)
	}
	var header struct {
		Kid string `json:"kid"`
	}
	if err := json.Unmarshal(raw, &header); err != nil {
		t.Fatalf("parse header: %v", err)
	}
	return header.Kid
}

func verifyViaJWKS(t *testing.T, keys []map[string]string, token string) {
	t.Helper()
	kid := kidOf(t, token)
	for _, entry := range keys {
		if entry["kid"] != kid {
			continue
		}
		n, err := base64.RawURLEncoding.DecodeString(entry["n"])
		if err != nil {
			t.Fatalf("decode modulus: %v", err)
		}
		e, err := base64.RawURLEncoding.DecodeString(entry["e"])
		if err != nil {
			t.Fatalf("decode exponent: %v", err)
		}
		public := &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(new(big.Int).SetBytes(e).Int64())}
		parts := strings.Split(token, ".")
		signature, err := base64.RawURLEncoding.DecodeString(parts[2])
		if err != nil {
			t.Fatalf("decode signature: %v", err)
		}
		digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
		if err := rsa.VerifyPKCS1v15(public, crypto.SHA256, digest[:], signature); err != nil {
			t.Fatalf("JWKS material failed to verify token: %v", err)
		}
		return
	}
	t.Fatalf("kid %s not present in JWKS", kid)
}
