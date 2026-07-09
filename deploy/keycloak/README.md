# Keycloak OIDC federation (roadmap W23)

PolyForge validates logins federated to Keycloak using the authorization-
code flow with PKCE (RFC 7636). The relying-party side lives in
`internal/auth/oidc.go`: discovery, JWKS with rotation-tolerant refetch,
S256 PKCE, and strict ID-token validation (RS256 pinned, iss/aud/exp/iat).

## Realm setup

1. `kubectl apply -f deploy/keycloak/keycloak.yaml`
2. In the admin console (`admin` / secret in `keycloak-admin`):
   - Create realm `polyforge`.
   - Create client `polyforge-web`: public client, Standard Flow enabled,
     *Proof Key for Code Exchange: S256* (Advanced settings), redirect URI
     `https://<polyforge-host>/auth/callback`.
   - Create a test user with a password.
3. Configure PolyForge:

```go
provider := auth.NewOIDCProvider("http://keycloak.keycloak.svc.cluster.local:8080/realms/polyforge", "polyforge-web")
```

Issuer discovery, JWKS, and the token endpoint all follow from the realm
URL — no other Keycloak-specific configuration exists in the codebase.

## Verification

The full flow — authorize with S256 challenge, code exchange with verifier
enforcement, JWKS signature validation, aud/iss/exp checks, tampered-token
rejection — runs in `internal/auth/oidc_test.go` against a spec-shaped
fake IdP. A live Keycloak login (the W23 end-to-end gate) is pending a
cluster; the wire protocol exercised is identical.
