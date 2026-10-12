package platform

import (
	"errors"
	"net/http"
	"strings"

	"polyforge/internal/auth"
	"polyforge/internal/tenant"
)

// issueToken exchanges a valid tenant API key for a short-lived RS256 access
// token plus a rotating refresh token. The JWT inherits the API key's scope,
// so a bearer token can never exceed the authority of the key that minted it.
func (s *Server) issueToken(w http.ResponseWriter, r *http.Request) {
	var input struct {
		TenantID string `json:"tenant_id"`
	}
	if err := readJSON(w, r, &input); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	if !validResourceID(input.TenantID) {
		s.writeError(w, r, http.StatusBadRequest, errors.New("tenant_id is required"))
		return
	}
	secret := strings.TrimSpace(r.Header.Get("X-PolyForge-API-Key"))
	if secret == "" {
		s.writeError(w, r, http.StatusUnauthorized, tenant.ErrUnauthorized)
		return
	}
	key, err := s.tenants.AuthenticateAPIKey(r.Context(), input.TenantID, secret)
	if err != nil {
		s.writeError(w, r, statusFor(err), err)
		return
	}
	pair, err := s.issuer.Issue(input.TenantID, tenant.NormalizeScope(key.Scope))
	if err != nil {
		s.writeError(w, r, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, http.StatusOK, pair)
}

// refreshToken rotates a refresh token. The presented token is consumed
// whether or not a new pair is returned, so replaying an old token yields
// 401 rather than a second live session.
func (s *Server) refreshToken(w http.ResponseWriter, r *http.Request) {
	var input struct {
		RefreshToken string `json:"refresh_token"`
	}
	if err := readJSON(w, r, &input); err != nil {
		s.writeError(w, r, http.StatusBadRequest, err)
		return
	}
	if strings.TrimSpace(input.RefreshToken) == "" {
		s.writeError(w, r, http.StatusBadRequest, errors.New("refresh_token is required"))
		return
	}
	pair, err := s.issuer.Refresh(input.RefreshToken)
	if err != nil {
		if errors.Is(err, auth.ErrInvalidRefreshToken) {
			s.writeError(w, r, http.StatusUnauthorized, err)
			return
		}
		s.writeError(w, r, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, http.StatusOK, pair)
}

// jwks publishes the RFC 7517 key set. It is unauthenticated by design:
// verifiers (other services, sidecars, Postgres) fetch public keys here so
// signing-key rotation propagates without restarting anything.
func (s *Server) jwks(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.issuer.JWKS())
}

// rotateSigningKeys swaps in a fresh signing key while keeping the previous
// key verifiable. Admin-only: rotation is an operational action, not a
// tenant capability. Returns the updated JWKS so the caller can confirm the
// new kid is live.
func (s *Server) rotateSigningKeys(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeAdmin(w, r) {
		return
	}
	if err := s.issuer.Rotate(); err != nil {
		s.writeError(w, r, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, http.StatusOK, s.issuer.JWKS())
}

// bearerToken extracts a token from the Authorization header, or "" if the
// header is absent or not a Bearer scheme.
func bearerToken(r *http.Request) string {
	header := strings.TrimSpace(r.Header.Get("Authorization"))
	if len(header) > 7 && strings.EqualFold(header[:7], "Bearer ") {
		return strings.TrimSpace(header[7:])
	}
	return ""
}

// bearerCredential resolves a JWT for tenantID: its scope, or the refusal. A
// bad token is 401 and a valid token for a different tenant is 403 — the
// contract authorizeTenant applies to API keys, which also checks the scope.
func (s *Server) bearerCredential(token, tenantID string) (scope string, status int, err error) {
	claims, err := s.issuer.Verify(token)
	if err != nil {
		return "", http.StatusUnauthorized, tenant.ErrUnauthorized
	}
	if claims.TenantID != tenantID {
		return "", http.StatusForbidden, tenant.ErrForbidden
	}
	return claims.Scope, 0, nil
}
