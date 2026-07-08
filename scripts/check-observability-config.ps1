$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$dashboardPath = Join-Path $root "deploy\observability\grafana\dashboards\polyforge-control-plane.json"
$prometheusPath = Join-Path $root "deploy\observability\prometheus\prometheus.yml"
$datasourcePath = Join-Path $root "deploy\observability\grafana\provisioning\datasources\polyforge-prometheus.yml"
$dashboardProviderPath = Join-Path $root "deploy\observability\grafana\provisioning\dashboards\polyforge.yml"

$dashboard = Get-Content -LiteralPath $dashboardPath -Raw | ConvertFrom-Json
if ($dashboard.uid -ne "polyforge-control-plane") {
    throw "Grafana dashboard UID is not polyforge-control-plane"
}
if ($dashboard.panels.Count -lt 5) {
    throw "Grafana dashboard must contain at least 5 panels"
}

$prometheus = Get-Content -LiteralPath $prometheusPath -Raw
foreach ($expected in @("job_name: polyforge-control-plane", "host.docker.internal:8080", "metrics_path: /metrics")) {
    if (-not $prometheus.Contains($expected)) {
        throw "Prometheus config is missing: $expected"
    }
}

$datasource = Get-Content -LiteralPath $datasourcePath -Raw
foreach ($expected in @("uid: polyforge-prometheus", "type: prometheus", "url: http://prometheus:9090")) {
    if (-not $datasource.Contains($expected)) {
        throw "Grafana datasource config is missing: $expected"
    }
}

$dashboardProvider = Get-Content -LiteralPath $dashboardProviderPath -Raw
if (-not $dashboardProvider.Contains("path: /var/lib/grafana/dashboards")) {
    throw "Grafana dashboard provider does not point at /var/lib/grafana/dashboards"
}

[pscustomobject]@{
    prometheus = "ok"
    grafana_datasource = "ok"
    grafana_dashboard = "ok"
    panels = $dashboard.panels.Count
}
