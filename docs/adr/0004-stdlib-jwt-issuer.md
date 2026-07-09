# ADR 0004: Stdlib RS256 JWT issuer with in-memory refresh tokens

Status: accepted · 2026-07-09

## Context

Roadmap W7 requires JWT issuance with RS256, refresh-token rotation, and a
JWKS endpoint, with OAuth2/OIDC via Ory Hydra or Keycloak for human users.
The repo's standing discipline (ADR 0001) is to prefer the standard library
over dependencies when the outcome is equivalent and the code stays small.

## Decision

1. **Hand-rolled JWS compact serialization on `crypto/rsa`** rather than
   `golang-jwt` or Hydra. The signing surface is ~40 lines; the verifier
   pins `alg` to RS256 before key lookup, which structurally eliminates the
   two classic JWT CVE classes (`alg=none`, RS/HS confusion) that libraries
   must guard against configurably. Tests cover both attacks explicitly.
2. **Refresh tokens are single-use, stored in memory as SHA-256 digests.**
   Rotation-with-replay-rejection is the property W7 asks for; persistence
   is not, yet. Consequence: a control-plane restart invalidates outstanding
   refresh sessions (clients fall back to their API key, which is the root
   credential anyway). When the auth service splits out (roadmap /services/
   auth), refresh state moves to Redis/Postgres.
3. **Signing keys are generated at startup and rotated via an admin
   endpoint**, keeping exactly one previous key verifiable. Tokens outlive a
   rotation; nothing outlives two. Keys are not persisted, so a restart
   invalidates outstanding *access* tokens after at most 15 minutes — the
   token TTL already bounds this window.
4. **OAuth2 Authorization Code + PKCE (Hydra/Keycloak) is deferred**, not
   skipped: it requires human users, which the platform does not have until
   a UI exists. Deferring whole rather than stubbing half is the same call
   made for this feature in session 3.

## Consequences

- Zero new dependencies; `go.sum` unchanged.
- The "JWT validates via JWKS without service restart" W7 gate is proven by
  `TestKeyRotationWithoutRestart` and `TestJWKSAndKeyRotationEndpoints`
  against a live in-process server rather than a second service — the
  cross-service version becomes testable once compose/kind runs (W9/W10).
- Anyone auditing against the roadmap should read "Ory Hydra" as
  deliberately not present.
