# ADR 0010: Secrets resolve through a chain; login federates via OIDC+PKCE

Status: accepted · 2026-07-09 · implemented and unit-tested; live Vault/Keycloak pending a cluster

## Context

W23 removes two classes of standing risk: long-lived secrets in env vars
and a home-grown login story. Vault holds the secrets; Keycloak owns
authentication. The question is how much of each vendor's machinery to
absorb into the codebase.

## Decisions

1. **A three-link `secrets.Source` chain, not the Vault SDK.**
   `Dir` (files injected by Vault Agent or a K8s Secret mount) → `Vault`
   (KV v2 over HTTP, Kubernetes auth: SA token in, short-lived Vault token
   out, renewed at 2/3 lease) → `Env` (local dev, unchanged). Application
   code asks for a name and cannot tell which layer answered. The file
   layer re-reads per lookup, so an agent-rewritten secret rotates with
   zero restarts — the mechanism behind the roadmap's "new credentials
   within 60s" gate, asserted in tests as immediate pickup.
2. **The HTTP surface is hand-written, mirroring ADR 0004.** Two Vault
   endpoints (login, KV read) and three OIDC endpoints (discovery, JWKS,
   token) do not justify SDK dependencies in a codebase whose JWT issuer
   is already stdlib.
3. **OIDC relying party with mandatory S256 PKCE.** `auth.OIDCProvider`
   does discovery, JWKS caching with rotation-tolerant refetch, code
   exchange carrying the verifier, and strict ID-token validation: alg
   pinned to RS256 (kills alg=none and RS/HS confusion), iss and aud
   exact, exp/iat with 2-minute skew. Keycloak is configuration, not a
   dependency: any spec-compliant IdP works, which is exactly what the
   test suite's fake IdP proves.
4. **gitleaks gates CI over full history.** "Zero hardcoded secrets" is
   enforced by a scanner, not a convention. The dev-mode manifests carry
   clearly-labeled placeholder credentials by design; they name themselves
   `dev-only` and are rotated via Vault in any shared environment.

## Consequences

- Rotating the admin key or a provider API key is now a Vault write.
- PolyForge's own RS256 issuer (ADR 0004) remains for tenant API traffic;
  OIDC covers human login. Two token authorities, deliberately: machine
  and human credentials rotate on different schedules and threat models.
- Live Vault-on-K8s and Keycloak logins are runbook steps
  (deploy/vault/README.md, deploy/keycloak/README.md) awaiting a cluster.
