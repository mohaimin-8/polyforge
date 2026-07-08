$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:GOCACHE = Join-Path $root ".gocache"
if (-not $env:POLYFORGE_ADMIN_KEY) {
    $env:POLYFORGE_ADMIN_KEY = "pf_admin_local"
}
go run ./cmd/control-plane
