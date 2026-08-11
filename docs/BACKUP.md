# Backup and restore (HUB-013)

## What is backed up, and why only this

`documents.sqlite3` (in the `hub_research_hub_data` volume) is the only
irreplaceable state in the stack: canonical documents, the research reports
(including the attempt-11 acceptance artifact), and the `chunk_fts` lexical
index. Everything else is rebuildable or transient:

- **Qdrant** is rebuilt from the document store:
  `docker exec hub-research-hub python -m app.rebuild` (embedding cost only;
  `--lexical-only` rebuilds just `chunk_fts`).
- **Redis** holds job/queue state of transient value; a lost queue means at
  worst re-submitting a research job.
- **Ollama models, images, config** are re-downloadable or in git.

The database runs in WAL mode, so a raw file copy is not consistent. Backups
are taken with `VACUUM INTO` inside the running container — no downtime, no
writer pause needed — then integrity-checked before they are kept.

## Scheduled backup

A Windows scheduled task `hub-stack-documents-backup` (registered 2026-08-11)
runs `scripts\backup.ps1` daily at 03:30, appending output to
`backups\backup.log`:

- Snapshot via `VACUUM INTO` inside `hub-research-hub`, then
  `PRAGMA integrity_check` plus non-empty `documents`/`research_reports`
  counts; an invalid snapshot is deleted and the run fails.
- Copied out to `backups\documents-<stamp>.sqlite3` (gitignored, ~46 MB at
  67 documents).
- Retention: newest 14 backups are kept (override with `-Retain`).
- Failures exit non-zero: visible in `backup.log` and in Task Scheduler's
  Last Run Result for the task.

Manual run: `powershell -File scripts\backup.ps1`. Measured runtime: ~3 s.

The destination stays on this machine, so backups are not encrypted (per the
backlog scope). If you later sync `backups\` to any off-machine destination,
encrypt first.

## Restore

### Test restore (safe, no live impact)

```powershell
powershell -File scripts\restore.ps1 -BackupFile backups\documents-<stamp>.sqlite3
```

Restores into a brand-new clean volume, verifies integrity, document/report
counts, and prints the newest report row. Measured recovery time: ~10 s.
Remove the test volume afterwards with the `docker volume rm` command it
prints. Last verified 2026-08-11: `integrity=ok documents=67 reports=13`,
attempt-11 report present.

### Live restore (disaster recovery)

```powershell
powershell -File scripts\restore.ps1 -BackupFile backups\documents-<stamp>.sqlite3 -Live
```

Stops `research-hub` and `research-worker`, replaces the database in
`hub_research_hub_data` (removing stale `-wal`/`-shm` files), verifies it,
restarts both services, and polls `/readyz` until all-true (3-minute limit).
No credentials are required beyond local Docker access. If the Qdrant
collection has diverged from the restored document set (documents ingested
after the backup was taken), rebuild vectors:

```powershell
docker exec hub-research-hub python -m app.rebuild
```
