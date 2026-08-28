# Name the host stall while it happens -- PREREG_LIVE_SOAK_V7.md (attempt 9).
#
# WHY THIS EXISTS. Attempt 7's 51.4 s stall was corroborated by a Windows
# Volsnap event 36 found in the host log AFTER the run. V6 answered half of
# that by measuring the freeze directly (`sample_gap_s`, `host_write_ms` in
# soak_observer.sh) -- attempt 8 recorded a 54 s scheduling gap and a 1412 ms
# host write, so the freeze is no longer inferred. But a measured gap still
# does not NAME its source: a 54 s excess looks identical whether VSS froze
# the volume, the disk stack reset, or the kernel suspended the box.
#
# This tails the Windows System log for exactly the providers that can produce
# that signature and writes each event to the evidence directory as it lands,
# so a stall in observer.csv can be joined to a named host event by timestamp
# instead of being reconstructed from a post-hoc log trawl.
#
# COST DISCIPLINE. The instrument must not become the load. One long-lived
# process for the whole sitting (not one launch per sample), a 60 s poll, and
# a time-filtered FilterHashtable query that the event log serves from its own
# index. Nothing here touches the cluster or the system under test.
param(
  [Parameter(Mandatory = $true)][string]$OutFile,
  [int]$IntervalSeconds = 60
)

$ErrorActionPreference = 'Continue'

# The providers that can stall a volume write or unschedule a process. Volsnap
# is the one attempt 7 and attempt 8 both implicate; the rest are present so a
# DIFFERENT cause is recorded as itself rather than being missed and silently
# attributed to VSS.
$ProviderPattern = 'volsnap|VSS|^disk$|Ntfs|volmgr|storahci|stornvme|Kernel-Power|Kernel-Boot|EventLog'

if (-not (Test-Path $OutFile)) {
  Set-Content -Path $OutFile -Value 'iso_utc,epoch,record_id,provider,event_id,level,message' -Encoding utf8
}

# Seed from whatever the file already holds, so a restarted tail does not
# duplicate rows into evidence that a record will later be scored from.
$seen = New-Object 'System.Collections.Generic.HashSet[int64]'
try {
  Import-Csv -Path $OutFile -ErrorAction Stop | ForEach-Object {
    $rid = 0L
    if ([int64]::TryParse($_.record_id, [ref]$rid)) { [void]$seen.Add($rid) }
  }
} catch { }

# Look back further than one interval on the first pass: if the host froze
# during startup, the event that says so is already in the log.
$lookback = [Math]::Max($IntervalSeconds * 3, 300)

while ($true) {
  try {
    $since = (Get-Date).AddSeconds(-$lookback)
    $events = Get-WinEvent -FilterHashtable @{ LogName = 'System'; StartTime = $since } -ErrorAction SilentlyContinue |
      Where-Object { $_.ProviderName -match $ProviderPattern }

    foreach ($e in $events) {
      if ($seen.Contains([int64]$e.RecordId)) { continue }
      [void]$seen.Add([int64]$e.RecordId)

      # One event is one CSV row: commas and newlines out of the message, so a
      # multi-line Volsnap description cannot corrupt the column layout.
      $msg = ($e.Message -replace '\s+', ' ') -replace ',', ';'
      if ($msg.Length -gt 400) { $msg = $msg.Substring(0, 400) }
      $utc = $e.TimeCreated.ToUniversalTime()
      $row = '{0},{1},{2},{3},{4},{5},{6}' -f `
        $utc.ToString('yyyy-MM-ddTHH:mm:ssZ'),
        [int64]([DateTimeOffset]$e.TimeCreated).ToUnixTimeSeconds(),
        $e.RecordId, $e.ProviderName, $e.Id, $e.LevelDisplayName, $msg
      Add-Content -Path $OutFile -Value $row -Encoding utf8
    }

    # Bound the dedup set on a 24 h sitting rather than growing it forever.
    if ($seen.Count -gt 20000) { $seen.Clear() }
  } catch {
    # An instrument that cannot take a sample writes nothing and tries again.
    # It must never be the reason a 24 h run ends.
  }
  Start-Sleep -Seconds $IntervalSeconds
  $lookback = $IntervalSeconds * 3
}
