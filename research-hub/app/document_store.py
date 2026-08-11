"""Persistent canonical source documents and derived-index checkpoints."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterator


# A lexical candidate term is kept only when it is rare in the index. With an
# OR over every topic token the lexical list mirrors the dense list, and under
# reciprocal rank fusion a lexical-only needle (one 1/(k+r) contribution) can
# never outrank chunks present in both channels - measured as hybrid == dense
# on the exact-term manifest. Rarity keeps the channel precise so fusion can
# actually lift needles.
LEXICAL_RARITY_FLOOR = 5
LEXICAL_RARITY_FRACTION = 0.01


def lexical_tokens(topic: str) -> list[str]:
    """Alphanumeric tokens only, so FTS5 operators and quotes cannot inject."""
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", topic)]


def selective_match_terms(
    db: sqlite3.Connection, topic: str, document_ids: list[str]
) -> list[str]:
    """Quoted unigrams and adjacent-bigram phrases rare enough to be needles.

    Rarity is measured within the retrieval scope, not corpus-wide: a term
    ubiquitous in the retained sources ("consort ai" in a CONSORT-AI job)
    would flood the lexical list and hand dense candidates dual-channel RRF
    boosts even though it is rare globally.
    """
    tokens = lexical_tokens(topic)
    if not tokens:
        return []
    placeholders = ",".join("?" for _ in document_ids)
    total = db.execute(
        f"SELECT COUNT(*) FROM chunk_fts WHERE document_id IN ({placeholders})",
        document_ids,
    ).fetchone()[0]
    if not total:
        return []
    threshold = max(LEXICAL_RARITY_FLOOR, int(total * LEXICAL_RARITY_FRACTION))
    candidates = list(dict.fromkeys(
        [f'"{token}"' for token in tokens]
        + [f'"{a} {b}"' for a, b in zip(tokens, tokens[1:])]
    ))
    eligible = []
    for phrase in candidates:
        frequency = db.execute(
            f"""SELECT COUNT(*) FROM chunk_fts
                WHERE chunk_fts MATCH ? AND document_id IN ({placeholders})""",
            (phrase, *document_ids),
        ).fetchone()[0]
        if 0 < frequency <= threshold:
            eligible.append((phrase, frequency))
    if not eligible:
        return []
    # Keep only the rarest band: a DF-6 word like "conduct" beside a DF-1
    # needle floods the list with topical chunks that then collect
    # dual-channel RRF boosts and outrank the needle - measured on the
    # delphimanager case. The band self-scales when no ultra-rare term exists.
    rarest = min(frequency for _phrase, frequency in eligible)
    return [
        phrase for phrase, frequency in eligible if frequency <= 2 * rarest
    ]


def search_chunk_index(
    db: sqlite3.Connection, topic: str, document_ids: list[str], limit: int
) -> list[dict]:
    """BM25-ranked needle-term search over chunk_fts, scoped and deterministic."""
    terms = selective_match_terms(db, topic, document_ids)
    if not terms or limit < 1:
        return []
    placeholders = ",".join("?" for _ in document_ids)
    rows = db.execute(
        f"""SELECT document_id, chunk_index, text
            FROM chunk_fts
            WHERE chunk_fts MATCH ? AND document_id IN ({placeholders})
            ORDER BY bm25(chunk_fts), document_id, chunk_index
            LIMIT ?""",
        (" OR ".join(terms), *document_ids, limit),
    ).fetchall()
    return [
        {
            "document_id": row["document_id"],
            "chunk_index": int(row["chunk_index"]),
            "text": row["text"],
        }
        for row in rows
    ]


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
                CREATE TABLE IF NOT EXISTS job_sources (
                    job_id TEXT NOT NULL,
                    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                    observed_at TEXT NOT NULL,
                    research_metadata TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(job_id, document_id)
                );
                CREATE INDEX IF NOT EXISTS job_sources_document_idx
                    ON job_sources(document_id, job_id);
                INSERT OR IGNORE INTO job_sources (
                    job_id, document_id, observed_at, research_metadata
                )
                SELECT job_id, document_id,
                       COALESCE(NULLIF(fetched_at, ''), created_at),
                       research_metadata
                FROM documents
                WHERE job_id <> '';
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
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    document_id UNINDEXED,
                    chunk_index UNINDEXED,
                    text,
                    tokenize='unicode61'
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
                    http_metadata=excluded.http_metadata
            """, (
                document["document_id"], document["canonical_url"], document["source_url"],
                document.get("title", ""), document["markdown"], document["content_hash"],
                document["fetched_at"], json.dumps(document.get("http_metadata", {})),
                document["extraction_version"], document["job_id"],
                json.dumps(document.get("research_metadata", {})), document["created_at"],
            ))

    def observe_job_source(
        self, job_id: str, document_id: str, observed_at: str,
        research_metadata: dict,
    ) -> None:
        with self._connect() as db:
            db.execute("""
                INSERT INTO job_sources (
                    job_id, document_id, observed_at, research_metadata
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(job_id, document_id) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    research_metadata=excluded.research_metadata
            """, (job_id, document_id, observed_at, json.dumps(research_metadata)))

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
                """SELECT documents.* FROM job_sources
                   JOIN documents USING(document_id)
                   WHERE job_sources.job_id = ?
                   ORDER BY documents.canonical_url, documents.document_id""",
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
            document_ids = [row[0] for row in db.execute(
                "SELECT document_id FROM documents WHERE canonical_url = ?",
                (canonical_url,),
            )]
            if document_ids:
                placeholders = ",".join("?" for _ in document_ids)
                db.execute(
                    f"DELETE FROM chunk_fts WHERE document_id IN ({placeholders})",
                    document_ids,
                )
            result = db.execute(
                "DELETE FROM documents WHERE canonical_url = ?", (canonical_url,)
            )
            return result.rowcount

    def replace_chunks(self, document_id: str, chunks: list[str]) -> None:
        """Replace a document's lexical rows with its full derived chunk set."""
        with self._connect() as db:
            db.execute("DELETE FROM chunk_fts WHERE document_id = ?", (document_id,))
            db.executemany(
                "INSERT INTO chunk_fts (document_id, chunk_index, text) VALUES (?, ?, ?)",
                [(document_id, index, text) for index, text in enumerate(chunks)],
            )

    def search_chunks(
        self, topic: str, document_ids: list[str], limit: int
    ) -> list[dict]:
        """BM25-ranked lexical candidates scoped to retained document identity."""
        if not document_ids:
            raise ValueError("lexical search requires retained source scope")
        with self._connect() as db:
            return search_chunk_index(db, topic, document_ids, limit)

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
