# Restore a documents.sqlite3 backup into a Docker volume (HUB-013).
#
# Default (no switches): restores into a brand-new clean volume and verifies
# it — a safe, repeatable restore test that never touches live data. The test
# volume is left in place for inspection; remove it with `docker volume rm`.
#
# -Live: restores into the live hub_research_hub_data volume. Stops the two
# writers (research-hub, research-worker) first, replaces the database,
# restarts them, and smoke-checks /readyz. Qdrant is NOT restored — if vectors
# and documents have diverged, rebuild with:
#   docker exec hub-research-hub python -m app.rebuild
param(
    [Parameter(Mandatory = $true)][string]$BackupFile,
    [string]$Volume = "",
    [switch]$Live
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupFile)) { throw "no such backup: $BackupFile" }
$image = "hub-research-hub:latest"
$backupDir = (Resolve-Path (Split-Path -Parent (Resolve-Path $BackupFile))).Path
$backupName = Split-Path $BackupFile -Leaf

if ($Live) {
    $Volume = "hub_research_hub_data"
} elseif (-not $Volume) {
    $Volume = "hub-restore-test-" + (Get-Date -Format "yyyyMMddHHmmss")
}

if ($Live) {
    Write-Output "stopping writers (research-hub, research-worker)..."
    docker compose stop research-hub research-worker
    if ($LASTEXITCODE -ne 0) { throw "failed to stop services (exit $LASTEXITCODE)" }
}

Write-Output "restoring $backupName into volume $Volume ..."
docker run --rm -v "${Volume}:/restore" -v "${backupDir}:/backup:ro" $image `
    sh -c "rm -f /restore/documents.sqlite3 /restore/documents.sqlite3-wal /restore/documents.sqlite3-shm && cp /backup/$backupName /restore/documents.sqlite3"
if ($LASTEXITCODE -ne 0) { throw "restore copy failed (exit $LASTEXITCODE)" }

# Windows PowerShell 5.1 mangles embedded double quotes in native-command
# arguments, so the Python below must use single quotes exclusively.
$verify = @'
import sqlite3, sys
db = sqlite3.connect('/restore/documents.sqlite3')
integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
docs = db.execute('select count(*) from documents').fetchone()[0]
reports = db.execute('select count(*) from research_reports').fetchone()[0]
job_id, topic = db.execute(
    'select job_id, topic from research_reports order by updated_at desc limit 1'
).fetchone()
print(f'integrity={integrity} documents={docs} reports={reports}')
print(f'latest report: {job_id} :: {topic[:60]}')
sys.exit(0 if integrity == 'ok' and docs > 0 and reports > 0 else 1)
'@
docker run --rm -v "${Volume}:/restore" $image python -c $verify
if ($LASTEXITCODE -ne 0) { throw "restored database failed verification (exit $LASTEXITCODE)" }

if ($Live) {
    Write-Output "restarting writers..."
    docker compose start research-hub research-worker
    if ($LASTEXITCODE -ne 0) { throw "failed to restart services (exit $LASTEXITCODE)" }
    $deadline = (Get-Date).AddMinutes(3)
    $ready = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        try {
            $ready = Invoke-RestMethod "http://localhost:8000/readyz" -TimeoutSec 5
            if ($ready.status -eq "ok") { break }
        } catch {}
    }
    if ($null -eq $ready -or $ready.status -ne "ok") { throw "/readyz did not return ok within 3 minutes" }
    Write-Output "/readyz: $($ready | ConvertTo-Json -Compress)"
    Write-Output "live restore complete. If vectors and documents diverged, run: docker exec hub-research-hub python -m app.rebuild"
} else {
    Write-Output "restore test complete into clean volume $Volume (remove with: docker volume rm $Volume)"
}
