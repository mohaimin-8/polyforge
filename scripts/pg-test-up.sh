#!/usr/bin/env bash
# Stand up the PostgreSQL instance the storage integration tests need, with
# the same roles and auth posture CI uses (.github/workflows/ci.yml).
#
# Why this exists: `internal/storage/postgres/store_integration_test.go`
# skips unless POLYFORGE_TEST_POSTGRES_{ADMIN,APP}_URL are set, so on a
# machine without Docker the row-level-security tests silently do not run —
# and RLS is the load-bearing control behind the paper's tenant-isolation
# claim (docs/SECURITY.md API1). "Skipped" reads identically to "passed" in a
# summary line, which is exactly the failure mode the eval gates exist to
# prevent. This script makes running them a one-liner.
#
#   ./scripts/pg-test-up.sh          # start + create roles + print exports
#   ./scripts/pg-test-up.sh --down   # tear down
#
# Then:
#   eval "$(./scripts/pg-test-up.sh --env)"
#   go test ./internal/storage/postgres/ -count=1 -v
set -euo pipefail

NAME="pf-pg-test"
IMAGE="postgres:18"

# Pick a free host port. 5432 is deliberately NOT the default: `compose.yaml`
# publishes its own postgres there, so a developer with the stack up would
# otherwise hit "Bind for 127.0.0.1:5432 failed: port is already allocated" —
# a confusing failure for a script whose whole job is to be one command.
# An explicit POLYFORGE_PG_TEST_PORT always wins.
port_free() {
    ! docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$1->"
}

# The port of an already-running test container, if there is one. This must
# take precedence over fresh selection: otherwise `--env` re-runs the search,
# finds the port taken *by the container it is meant to describe*, and prints
# a URL pointing at the next port along — so the exports and the database
# disagree and every test fails to connect.
running_port() {
    docker ps --filter "name=^${NAME}$" --format '{{.Ports}}' 2>/dev/null |
        sed -n 's/.*:\([0-9]\+\)->5432\/tcp.*/\1/p' | head -1
}

if [ -n "${POLYFORGE_PG_TEST_PORT:-}" ]; then
    PORT="$POLYFORGE_PG_TEST_PORT"
elif [ -n "$(running_port)" ]; then
    PORT="$(running_port)"
else
    PORT=""
    for candidate in 5433 5434 5435 5436 5432; do
        if port_free "$candidate"; then
            PORT="$candidate"
            break
        fi
    done
    if [ -z "$PORT" ]; then
        echo "no free port found in 5432-5436; set POLYFORGE_PG_TEST_PORT" >&2
        exit 1
    fi
fi
ADMIN_URL="postgres://polyforge_admin@127.0.0.1:${PORT}/polyforge?sslmode=disable"
APP_URL="postgres://polyforge_app@127.0.0.1:${PORT}/polyforge?sslmode=disable"

print_env() {
    echo "export POLYFORGE_TEST_POSTGRES_ADMIN_URL='${ADMIN_URL}'"
    echo "export POLYFORGE_TEST_POSTGRES_APP_URL='${APP_URL}'"
}

case "${1:-up}" in
--down)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    echo "removed $NAME"
    exit 0
    ;;
--env)
    print_env
    exit 0
    ;;
esac

docker rm -f "$NAME" >/dev/null 2>&1 || true

# POSTGRES_HOST_AUTH_METHOD=trust because the test URLs carry no password,
# matching CI. Never use this posture for anything but a throwaway local
# test database — it accepts any connection for any role.
docker run -d --name "$NAME" \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    -e POSTGRES_DB=polyforge \
    -p "${PORT}:5432" \
    "$IMAGE" >/dev/null

printf 'waiting for postgres'
for _ in $(seq 1 60); do
    if docker exec "$NAME" pg_isready -U postgres -d polyforge >/dev/null 2>&1; then
        echo " ready"
        break
    fi
    printf '.'
    sleep 2
done

# Same roles as CI: admin BYPASSRLS (migrations, admin-authenticated reads),
# app NOBYPASSRLS (every tenant-scoped query, so a missed WHERE cannot leak).
# CREATEDB is test-only and never granted in the cluster: the eval-export
# parity test needs a database of its own, because eval-export scores the
# WHOLE store by definition, so a tenant created by a concurrent test in
# another package would land inside its aggregates.
# The store refuses to boot if these two are not distinct and correctly
# privileged (internal/storage/postgres/store.go).
docker exec -i "$NAME" psql -U postgres -d polyforge -v ON_ERROR_STOP=1 <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'polyforge_admin') THEN
    CREATE ROLE polyforge_admin LOGIN BYPASSRLS CREATEDB;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'polyforge_app') THEN
    CREATE ROLE polyforge_app LOGIN NOBYPASSRLS;
  END IF;
END $$;
ALTER ROLE polyforge_admin CREATEDB;
ALTER DATABASE polyforge OWNER TO polyforge_admin;
SQL

echo "roles ready. Run the integration tests with:"
echo
print_env
echo
echo "  go test ./internal/storage/postgres/ -count=1 -v"
