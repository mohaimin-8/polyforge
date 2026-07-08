param(
    [int]$TenantCount = 4,
    [int]$ProjectsPerTenant = 25,
    [string]$BaseUrl = "http://localhost:8080",
    [string]$OutputPath = "",
    [switch]$UseExistingServer
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($TenantCount -lt 1) {
    throw "TenantCount must be at least 1"
}
if ($ProjectsPerTenant -lt 1) {
    throw "ProjectsPerTenant must be at least 1"
}
if (-not $OutputPath) {
    $OutputPath = Join-Path $root "artifacts\m1-crud-baseline.json"
}

$adminKey = "pf_admin_bench"
$binaryPath = Join-Path $root "tmp\control-plane-bench.exe"
$dbPath = Join-Path $root "tmp\crud-load.db"
$process = $null
$samples = New-Object "System.Collections.Generic.List[object]"

function Percentile {
    param(
        [double[]]$Values,
        [double]$P
    )
    if ($Values.Count -eq 0) {
        return 0
    }
    $sorted = @($Values | Sort-Object)
    $index = [int][Math]::Ceiling(($P / 100) * $sorted.Count) - 1
    if ($index -lt 0) {
        $index = 0
    }
    if ($index -ge $sorted.Count) {
        $index = $sorted.Count - 1
    }
    return [Math]::Round([double]$sorted[$index], 2)
}

function Invoke-Tracked {
    param(
        [string]$Operation,
        [string]$Method,
        [string]$Uri,
        [hashtable]$Headers,
        [string]$Body = ""
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $args = @{
            Method = $Method
            Uri = $Uri
            Headers = $Headers
        }
        if ($Body) {
            $args.ContentType = "application/json"
            $args.Body = $Body
        }
        return Invoke-RestMethod @args
    }
    finally {
        $stopwatch.Stop()
        $samples.Add([pscustomobject]@{
            operation = $Operation
            latency_ms = [Math]::Round($stopwatch.Elapsed.TotalMilliseconds, 2)
        })
    }
}

if (-not $UseExistingServer) {
    $env:GOCACHE = Join-Path $root ".gocache"
    $env:POLYFORGE_ADMIN_KEY = $adminKey
    $env:POLYFORGE_DB_PATH = $dbPath
    $env:POLYFORGE_RATE_LIMIT_RPM = "100000"
    $env:POLYFORGE_RATE_LIMIT_BURST = "10000"

    New-Item -ItemType Directory -Force -Path (Split-Path $dbPath) | Out-Null
    Remove-Item -LiteralPath $dbPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $binaryPath -Force -ErrorAction SilentlyContinue
    go build -buildvcs=false -o $binaryPath ./cmd/control-plane

    $process = Start-Process -FilePath $binaryPath -WorkingDirectory $root -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 4
}
elseif ($env:POLYFORGE_ADMIN_KEY) {
    $adminKey = $env:POLYFORGE_ADMIN_KEY
}

try {
    $adminHeaders = @{ "X-PolyForge-Admin-Key" = $adminKey }
    $runID = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $tenantKeys = @{}
    $timer = [System.Diagnostics.Stopwatch]::StartNew()

    for ($tenantIndex = 1; $tenantIndex -le $TenantCount; $tenantIndex++) {
        $tenantID = "bench-$runID-$tenantIndex"
        $tenant = Invoke-Tracked `
            -Operation "tenant_create" `
            -Method "Post" `
            -Uri "$BaseUrl/v1/tenants" `
            -Headers $adminHeaders `
            -Body "{`"id`":`"$tenantID`",`"name`":`"Benchmark Tenant $tenantIndex`"}"
        $tenantKeys[$tenantID] = $tenant.bootstrap_api_key.secret
    }

    foreach ($tenantID in $tenantKeys.Keys) {
        $tenantHeaders = @{ "X-PolyForge-API-Key" = $tenantKeys[$tenantID] }
        for ($projectIndex = 1; $projectIndex -le $ProjectsPerTenant; $projectIndex++) {
            $projectID = "p-$projectIndex"
            Invoke-Tracked `
                -Operation "project_create" `
                -Method "Post" `
                -Uri "$BaseUrl/v1/tenants/$tenantID/projects" `
                -Headers $tenantHeaders `
                -Body "{`"id`":`"$projectID`",`"name`":`"Project $projectIndex`"}" | Out-Null
            Invoke-Tracked `
                -Operation "project_get" `
                -Method "Get" `
                -Uri "$BaseUrl/v1/tenants/$tenantID/projects/$projectID" `
                -Headers $tenantHeaders | Out-Null
            Invoke-Tracked `
                -Operation "project_update" `
                -Method "Patch" `
                -Uri "$BaseUrl/v1/tenants/$tenantID/projects/$projectID" `
                -Headers $tenantHeaders `
                -Body "{`"name`":`"Updated Project $projectIndex`"}" | Out-Null
            Invoke-Tracked `
                -Operation "project_list" `
                -Method "Get" `
                -Uri "$BaseUrl/v1/tenants/$tenantID/projects?limit=50" `
                -Headers $tenantHeaders | Out-Null
            Invoke-Tracked `
                -Operation "project_delete" `
                -Method "Delete" `
                -Uri "$BaseUrl/v1/tenants/$tenantID/projects/$projectID" `
                -Headers $tenantHeaders | Out-Null
        }
    }

    $timer.Stop()
    $latencies = @($samples | ForEach-Object { [double]$_.latency_ms })
    $operationGroups = $samples | Group-Object operation | ForEach-Object {
        $values = @($_.Group | ForEach-Object { [double]$_.latency_ms })
        [pscustomobject]@{
            operation = $_.Name
            count = $_.Count
            p50_ms = Percentile -Values $values -P 50
            p95_ms = Percentile -Values $values -P 95
            p99_ms = Percentile -Values $values -P 99
        }
    }

    $artifact = [pscustomobject]@{
        generated_at = [DateTimeOffset]::UtcNow.ToString("o")
        base_url = $BaseUrl
        tenant_count = $TenantCount
        projects_per_tenant = $ProjectsPerTenant
        total_requests = $samples.Count
        duration_seconds = [Math]::Round($timer.Elapsed.TotalSeconds, 2)
        requests_per_second = [Math]::Round($samples.Count / [Math]::Max($timer.Elapsed.TotalSeconds, 0.001), 2)
        latency_ms = [pscustomobject]@{
            p50 = Percentile -Values $latencies -P 50
            p95 = Percentile -Values $latencies -P 95
            p99 = Percentile -Values $latencies -P 99
        }
        operations = $operationGroups
        runtime = [pscustomobject]@{
            os = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
            process_architecture = [System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString()
            powershell = $PSVersionTable.PSVersion.ToString()
        }
    }

    New-Item -ItemType Directory -Force -Path (Split-Path $OutputPath) | Out-Null
    $artifact | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputPath -Encoding utf8
    $artifact
}
finally {
    if ($process) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    if (-not $UseExistingServer) {
        Remove-Item -LiteralPath $binaryPath -Force -ErrorAction SilentlyContinue
    }
}
