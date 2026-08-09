"""Persistent canonical source documents and derived-index checkpoints."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterator


class DocumentStore:
    """Small local source-of-truth store backed by transactional SQLite."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    markdown TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    http_metadata TEXT NOT NULL DEFAULT '{}',
                    extraction_version TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    research_metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(canonical_url, content_hash)
                );
                CREATE INDEX IF NOT EXISTS documents_url_idx
                    ON documents(canonical_url, fetched_at DESC);
                CREATE TABLE IF NOT EXISTS index_checkpoints (
                    index_name TEXT NOT NULL,
                    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                    chunker_version TEXT NOT NULL,
                    completed_chunks INTEGER NOT NULL DEFAULT 0,
                    total_chunks INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(index_name, document_id, chunker_version)
                );
                CREATE TABLE IF NOT EXISTS research_reports (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    report_markdown TEXT,
                    sources TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

    def save(self, document: dict) -> None:
        with self._connect() as db:
            db.execute("""
                INSERT INTO documents (
                    document_id, canonical_url, source_url, title, markdown,
                    content_hash, fetched_at, http_metadata, extraction_version,
                    job_id, research_metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    title=excluded.title, fetched_at=excluded.fetched_at,
                    http_metadata=excluded.http_metadata, job_id=excluded.job_id
            """, (
                document["document_id"], document["canonical_url"], document["source_url"],
                document.get("title", ""), document["markdown"], document["content_hash"],
                document["fetched_at"], json.dumps(document.get("http_metadata", {})),
                document["extraction_version"], document["job_id"],
                json.dumps(document.get("research_metadata", {})), document["created_at"],
            ))

    def get(self, document_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def iter_documents(self) -> Iterator[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM documents ORDER BY fetched_at, document_id").fetchall()
        for row in rows:
            yield self._decode(row)

    def documents_for_job(self, job_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM documents WHERE job_id = ? ORDER BY canonical_url, document_id",
                (job_id,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def get_report(self, job_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_reports WHERE job_id = ?", (job_id,)
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["sources"] = json.loads(value["sources"])
        return value

    def save_report(self, report: dict) -> None:
        with self._connect() as db:
            db.execute("""
                INSERT INTO research_reports (
                    job_id, status, topic, report_markdown, sources, error,
                    attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status, topic=excluded.topic,
                    report_markdown=excluded.report_markdown,
                    sources=excluded.sources, error=excluded.error,
                    attempts=excluded.attempts, updated_at=excluded.updated_at
            """, (
                report["job_id"], report["status"], report["topic"],
                report.get("report_markdown"), json.dumps(report.get("sources", [])),
                report.get("error"), report.get("attempts", 0),
                report["created_at"], report["updated_at"],
            ))

    def delete_url(self, canonical_url: str) -> int:
        with self._connect() as db:
            result = db.execute(
                "DELETE FROM documents WHERE canonical_url = ?", (canonical_url,)
            )
            return result.rowcount

    def checkpoint(self, index_name: str, document_id: str, chunker_version: str) -> int:
        with self._connect() as db:
            row = db.execute("""
                SELECT completed_chunks FROM index_checkpoints
                WHERE index_name = ? AND document_id = ? AND chunker_version = ?
            """, (index_name, document_id, chunker_version)).fetchone()
        return int(row[0]) if row else 0

    def set_checkpoint(
        self, index_name: str, document_id: str, chunker_version: str,
        completed_chunks: int, total_chunks: int, updated_at: str,
    ) -> None:
        with self._connect() as db:
            db.execute("""
                INSERT INTO index_checkpoints
                    (index_name, document_id, chunker_version, completed_chunks,
                     total_chunks, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(index_name, document_id, chunker_version) DO UPDATE SET
                    completed_chunks=excluded.completed_chunks,
                    total_chunks=excluded.total_chunks, updated_at=excluded.updated_at
            """, (index_name, document_id, chunker_version, completed_chunks,
                    total_chunks, updated_at))

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["http_metadata"] = json.loads(value["http_metadata"])
        value["research_metadata"] = json.loads(value["research_metadata"])
        return value
