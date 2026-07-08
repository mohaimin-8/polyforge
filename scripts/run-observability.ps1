$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

.\scripts\check-observability-config.ps1 | Out-Host

docker compose --profile observability up -d prometheus grafana

[pscustomobject]@{
    prometheus = "http://localhost:9090"
    grafana = "http://localhost:3000"
    grafana_user = "admin"
    grafana_password = "polyforge-local"
    target = "http://host.docker.internal:8080/metrics"
}
