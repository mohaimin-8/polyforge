# Vault (roadmap W23)

PolyForge resolves secrets through `internal/secrets`: Vault-Agent-injected
files first, then Vault's HTTP KV, then process env. Pods authenticate to
Vault with their ServiceAccount token (Kubernetes auth method) and receive
short-lived Vault tokens — no long-lived credential is mounted anywhere.

## Setup on the dev cluster

```sh
kubectl apply -f deploy/vault/vault.yaml
kubectl -n vault exec deploy/vault -- sh -c '
  export VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=polyforge-dev-only-root
  vault auth enable kubernetes
  vault write auth/kubernetes/config kubernetes_host=https://kubernetes.default.svc
  vault policy write polyforge - <<EOF
path "secret/data/polyforge" { capabilities = ["read"] }
EOF
  vault write auth/kubernetes/role/polyforge \
    bound_service_account_names=default \
    bound_service_account_namespaces=polyforge \
    policies=polyforge ttl=1h
  vault kv put secret/polyforge \
    POLYFORGE_ADMIN_KEY=... POLYFORGE_LLM_API_KEY=... GROQ_API_KEY=...
'
```

## Point PolyForge at it

```yaml
env:
  - name: POLYFORGE_VAULT_ADDR
    value: http://vault.vault.svc.cluster.local:8200
  - name: POLYFORGE_VAULT_K8S_ROLE
    value: polyforge
```

With `POLYFORGE_SECRETS_DIR` set instead (Vault Agent sidecar writing
`/vault/secrets/<NAME>` files), rotation needs no restart: the source
re-reads files per lookup — `TestDirSourceRereadsOnRotation` proves the
pickup is immediate, beating the roadmap's 60-second gate.

## Status

The secrets client (K8s login, KV v2 read, chain fallback) is implemented
and unit-tested against a mock Vault. The manifest and this runbook are
**not yet executed against a live cluster** (no Docker/K8s on this
machine); the rotation gate is asserted at the file layer in tests.
