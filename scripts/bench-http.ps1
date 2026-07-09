param(
    [int]$DurationSeconds = 15,
    [int]$Concurrency = 0,
    [string]$TargetPath = "/healthz",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($DurationSeconds -lt 1) {
    throw "DurationSeconds must be at least 1"
}
if (-not $OutputPath) {
    $OutputPath = Join-Path $root "artifacts\m1-http-baseline.json"
}

$serverBinary = Join-Path $root "tmp\control-plane-bench.exe"
$loadgenBinary = Join-Path $root "tmp\loadgen.exe"
$dbPath = Join-Path $root "tmp\http-bench.db"
$process = $null

$env:GOCACHE = Join-Path $root ".gocache"
$env:POLYFORGE_ADMIN_KEY = "pf_admin_bench"
$env:POLYFORGE_DB_PATH = $dbPath
# Rate limiting off and access logs at warn: the benchmark measures the
# handler path, not the logger or the limiter's rejection path.
$env:POLYFORGE_RATE_LIMIT_RPM = "0"
$env:POLYFORGE_LOG_LEVEL = "warn"

New-Item -ItemType Directory -Force -Path (Split-Path $dbPath) | Out-Null
Remove-Item -LiteralPath $dbPath -Force -ErrorAction SilentlyContinue
go build -buildvcs=false -o $serverBinary ./cmd/control-plane
go build -buildvcs=false -o $loadgenBinary ./cmd/loadgen

try {
    $process = Start-Process -FilePath $serverBinary -WorkingDirectory $root -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 3

    $arguments = @(
        "-url", "http://localhost:8080$TargetPath",
        "-duration", "${DurationSeconds}s",
        "-out", $OutputPath
    )
    if ($Concurrency -gt 0) {
        $arguments += @("-concurrency", "$Concurrency")
    }
    & $loadgenBinary @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "loadgen reported errors (exit $LASTEXITCODE)"
    }
}
finally {
    if ($process) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $serverBinary -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $loadgenBinary -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $dbPath -Force -ErrorAction SilentlyContinue
}
