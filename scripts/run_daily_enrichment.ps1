# Standing daily enrichment job — OI-7 (docs/PROGRESS.md).
#
# Decision (2026-09-16): run at the current ~1,000/day Google Books quota
# rather than sit idle waiting for the pending quota-increase request, and
# have it speed up automatically the moment that increase lands, with no
# code change needed when it does.
#
# The acceleration is free by construction, not a separate feature: --limit
# below is set far above any plausible daily quota (current ceiling ~1,000
# queries/day, ~1-2 per book; 22,843 books still pending as of 2026-09-16).
# scripts.enrich's own QuotaExceeded (services/providers/base.py) already
# stops the run the instant Google's real daily ceiling is hit, whatever
# that ceiling is on the day the job runs. A bigger quota just means
# QuotaExceeded fires later in the same run — nothing here needs to detect
# or react to the increase itself.
#
# This script's own job is narrower: get the datastores up (this machine's
# Docker Desktop does not survive a reboot or sleep — F-30, OI-8) and fail
# loudly and specifically rather than let scripts.enrich crash on a raw
# connection timeout when they are not.

$RepoRoot   = "D:\final\final\final_clean"
$BackendDir = Join-Path $RepoRoot "backend"
$Python     = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir     = Join-Path $RepoRoot "logs\enrichment"
$DockerExe  = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# The daily cap. Deliberately far above the ~1,000/day quota and the current
# pending count (22,843) — see the header. Raise this only if the catalogue
# itself grows past it; it is not the quota control.
$DailyLimit = 25000

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp      = Get-Date -Format "yyyy-MM-dd_HHmmss"
$RunLog     = Join-Path $LogDir "run_$Stamp.log"
$HistoryLog = Join-Path $LogDir "history.log"

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $RunLog -Value $line
}

function Write-History($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $HistoryLog -Value $line
}

Write-Log "Standing enrichment job starting (OI-7)."

# --- Step 1: Docker Desktop itself. This machine also runs an unrelated
# project (finmentor) on the same Docker daemon (F-24-adjacent: shared host,
# not shared interpreter) — starting the daemon is fine to share, but the
# containers touched below are named specifically so this job never starts
# or stops anything that isn't digikitab's own.
$dockerProc = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if (-not $dockerProc) {
    Write-Log "Docker Desktop is not running. Launching it."
    Start-Process $DockerExe
}

$engineReady = $false
for ($i = 0; $i -lt 24; $i++) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $engineReady = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $engineReady) {
    Write-Log "Docker engine did not become reachable after 2 minutes. Skipping this run."
    Write-History "SKIPPED  docker engine unreachable"
    exit 1
}
Write-Log "Docker engine is reachable."

# --- Step 2: the digikitab datastores specifically.
docker start digikitab-postgres digikitab-redis *> $null

$dbReady = $false
for ($i = 0; $i -lt 24; $i++) {
    docker exec digikitab-postgres pg_isready -U digikitab -d digikitab *> $null
    if ($LASTEXITCODE -eq 0) { $dbReady = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $dbReady) {
    Write-Log "Postgres did not become ready after 2 minutes. Skipping this run."
    Write-History "SKIPPED  postgres unreachable"
    exit 1
}
Write-Log "Postgres is ready. Running scripts.enrich --limit $DailyLimit."

# --- Step 3: the actual enrichment pass.
# POSTGRES_DB set explicitly rather than relied on as config.py's default —
# this session already lost real time twice to a POSTGRES_DB assumption that
# turned out to be wrong (F-47/F-48 writeups, docs/PROGRESS.md); a scheduled
# task's environment is not something to trust implicitly. digikitab, never
# digikitab_test — that name is conftest.py's, for pytest only.
$env:POSTGRES_DB = "digikitab"

$StdOutFile = "$RunLog.stdout.tmp"
$StdErrFile = "$RunLog.stderr.tmp"

Push-Location $BackendDir
try {
    # Start-Process with explicit redirect files, not `& $Python ... 2>&1`:
    # PowerShell 5.1 wraps a native process's stderr lines (where Python's
    # `logging` module writes by default) in NativeCommandError objects when
    # captured through its own pipeline, which showed up as spurious noise in
    # testing even when only the exit code mattered. Start-Process redirects
    # both streams to files at the OS level instead, so neither PowerShell's
    # error stream nor $LASTEXITCODE's reliability depends on this.
    $proc = Start-Process -FilePath $Python `
        -ArgumentList @("-m", "scripts.enrich", "--limit", $DailyLimit, "--verbose") `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $StdOutFile -RedirectStandardError $StdErrFile
    $ExitCode = $proc.ExitCode
} finally {
    Pop-Location
}

$Output = @()
if (Test-Path $StdErrFile) {
    Get-Content $StdErrFile | Add-Content -Path $RunLog
    Remove-Item $StdErrFile
}
if (Test-Path $StdOutFile) {
    $Output = Get-Content $StdOutFile
    $Output | Add-Content -Path $RunLog
    Remove-Item $StdOutFile
}

$Summary = $Output | Where-Object {
    $_ -match "processed|quota_exhausted|throttled|^\s*ok\s|^\s*partial\s|^\s*failed\s|description_coverage|english_still_pending"
}
Write-History "exit=$ExitCode"
foreach ($line in $Summary) { Write-History "    $line" }

Write-Log "Done. Exit code $ExitCode."
exit $ExitCode
