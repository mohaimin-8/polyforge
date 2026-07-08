$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required for the PostgreSQL integration suite."
}

$env:GOCACHE = Join-Path $root ".gocache"
$env:POLYFORGE_TEST_POSTGRES_ADMIN_URL = "postgres://polyforge_admin@localhost:5432/polyforge?sslmode=disable"
$env:POLYFORGE_TEST_POSTGRES_APP_URL = "postgres://polyforge_app@localhost:5432/polyforge?sslmode=disable"

docker compose up -d --wait postgres
try {
    go test ./internal/storage/postgres -run TestPostgresRowLevelIsolation -count=1 -v
}
finally {
    docker compose down -v
}
