# Consistent scheduled backup of documents.sqlite3 (HUB-013).
#
# documents.sqlite3 is the only irreplaceable state in the stack (canonical
# documents, research reports, chunk_fts). It runs in WAL mode, so a raw file
# copy is not consistent; this script snapshots it with VACUUM INTO inside the
# running container, integrity-checks the snapshot, copies it out, and prunes
# old backups. Qdrant is deliberately not backed up: it is rebuilt from this
# database with `python -m app.rebuild` (embedding cost only).
#
# Exit code is non-zero on any failure; the scheduled task appends all output
# to backups\backup.log, so a failed run is visible there and in Task
# Scheduler's Last Run Result.
param(
    [string]$BackupDir = (Join-Path $PSScriptRoot "..\backups"),
    [int]$Retain = 14,
    [string]$Container = "hub-research-hub"
)
$ErrorActionPreference = "Stop"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force $BackupDir | Out-Null
$BackupDir = (Resolve-Path $BackupDir).Path

# Windows PowerShell 5.1 mangles embedded double quotes in native-command
# arguments, so the Python below must use single quotes exclusively.
$snapshot = @'
import sqlite3, os, sys
src = '/app/data/documents.sqlite3'
tmp = '/app/data/.backup-tmp.sqlite3'
if os.path.exists(tmp):
    os.remove(tmp)
db = sqlite3.connect(src)
db.execute('VACUUM INTO ?', (tmp,))
db.close()
snap = sqlite3.connect(tmp)
integrity = snap.execute('PRAGMA integrity_check').fetchone()[0]
docs = snap.execute('select count(*) from documents').fetchone()[0]
reports = snap.execute('select count(*) from research_reports').fetchone()[0]
snap.close()
if integrity != 'ok' or docs < 1 or reports < 1:
    print(f'snapshot invalid: integrity={integrity} documents={docs} reports={reports}', file=sys.stderr)
    os.remove(tmp)
    sys.exit(1)
print(f'snapshot ok: documents={docs} reports={reports}')
'@

docker exec $Container python -c $snapshot
if ($LASTEXITCODE -ne 0) { throw "backup snapshot failed in $Container (exit $LASTEXITCODE)" }

$target = Join-Path $BackupDir "documents-$stamp.sqlite3"
docker cp "${Container}:/app/data/.backup-tmp.sqlite3" $target
if ($LASTEXITCODE -ne 0) { throw "docker cp of snapshot failed (exit $LASTEXITCODE)" }
docker exec $Container rm -f /app/data/.backup-tmp.sqlite3

$backups = Get-ChildItem $BackupDir -Filter "documents-*.sqlite3" | Sort-Object Name -Descending
foreach ($old in ($backups | Select-Object -Skip $Retain)) {
    Remove-Item $old.FullName -Confirm:$false
    Write-Output "pruned: $($old.Name)"
}

$sizeMb = [math]::Round((Get-Item $target).Length / 1MB, 1)
$kept = [math]::Min($backups.Count, $Retain)
Write-Output "$(Get-Date -Format o) backup complete: $target ($sizeMb MB); $kept backups retained"
