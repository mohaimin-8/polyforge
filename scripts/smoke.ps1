$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:GOCACHE = Join-Path $root ".gocache"
$env:POLYFORGE_ADMIN_KEY = "pf_admin_smoke"
$env:POLYFORGE_DB_PATH = Join-Path $root "tmp\smoke.db"
$binaryPath = Join-Path $root "tmp\control-plane-smoke.exe"

New-Item -ItemType Directory -Force -Path (Split-Path $env:POLYFORGE_DB_PATH) | Out-Null
Remove-Item -LiteralPath $env:POLYFORGE_DB_PATH -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $binaryPath -Force -ErrorAction SilentlyContinue
go build -buildvcs=false -o $binaryPath ./cmd/control-plane

$process = Start-Process -FilePath $binaryPath -WorkingDirectory $root -WindowStyle Hidden -PassThru
try {
    Start-Sleep -Seconds 4

    $adminHeaders = @{ "X-PolyForge-Admin-Key" = $env:POLYFORGE_ADMIN_KEY }
    $tenantResponse = Invoke-RestMethod -Method Post -Uri "http://localhost:8080/v1/tenants" -Headers $adminHeaders -ContentType "application/json" -Body '{"id":"smoke","name":"Smoke Tenant"}'
    $tenantKey = $tenantResponse.bootstrap_api_key.secret
    $tenantHeaders = @{ "X-PolyForge-API-Key" = $tenantKey }

    Invoke-RestMethod -Method Post -Uri "http://localhost:8080/v1/tenants/smoke/projects" -Headers $tenantHeaders -ContentType "application/json" -Body '{"id":"p1","name":"Smoke Project"}' | Out-Null
	$project = Invoke-RestMethod -Method Patch -Uri "http://localhost:8080/v1/tenants/smoke/projects/p1" -Headers $tenantHeaders -ContentType "application/json" -Body '{"name":"Updated Smoke Project"}'
    Invoke-RestMethod -Method Post -Uri "http://localhost:8080/v1/telemetry" -Headers $tenantHeaders -ContentType "application/json" -Body '{"tenant_id":"smoke","service":"ai-gateway","rps_window":22,"payload_bytes":12000,"latency_ms":450,"cache_hit":true,"embedding_density":0.72,"model_tier":"mid","child_spans":1}' | Out-Null

	$rotatedKey = Invoke-RestMethod -Method Post -Uri "http://localhost:8080/v1/tenants/smoke/api-keys/$($tenantResponse.bootstrap_api_key.id)/rotate" -Headers $tenantHeaders
	$tenantHeaders = @{ "X-PolyForge-API-Key" = $rotatedKey.secret }

    $profile = Invoke-RestMethod -Uri "http://localhost:8080/v1/tenants/smoke/workload-profile" -Headers $tenantHeaders
    $policy = Invoke-RestMethod -Uri "http://localhost:8080/v1/tenants/smoke/policy-recommendation" -Headers $tenantHeaders
    $metricsResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8080/metrics"
    $metrics = $metricsResponse.Content
    if (-not $metrics.Contains("polyforge_http_requests_total") -or -not $metrics.Contains("polyforge_telemetry_events_total")) {
        throw "metrics endpoint did not expose expected PolyForge series"
    }
    if (-not ($metricsResponse.Headers["traceparent"] -match "^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")) {
        throw "metrics response did not include a valid traceparent header"
    }

    [pscustomobject]@{
        tenant = $tenantResponse.tenant.id
		project = $project.name
        workload = $profile.label
        replicas = $policy.replicas
        cache_mb = $policy.cache_mb
        model_tier = $policy.model_tier
        metrics = "ok"
        traceparent = "ok"
    }
}
finally {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $binaryPath -Force -ErrorAction SilentlyContinue
}
