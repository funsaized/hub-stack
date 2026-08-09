# Canonical documents, rebuilds, and retention

Research Hub retains every successfully extracted source in
`/app/data/documents.sqlite3`, inside the `research_hub_data` Docker volume. Qdrant
is a derived index; SQLite is the source of truth for rebuilding it.

Each retained version contains the canonical and fetched URLs, exact cleaned
Markdown, title, SHA-256 content hash, fetch time, available HTTP metadata,
extraction version, job ID, topic, and tags. Content versions are retained
indefinitely by default so extraction changes can be audited and compared.

The same SQLite database stores each job's synthesis artifact. A report records its
topic, Markdown body, stable source/evidence mapping, status, error, timestamps, and
attempt count. Material findings and disagreements must cite retained evidence IDs;
unknowns explicitly describe insufficient evidence.

Retrieve or independently regenerate a completed job's report:

```bash
research report JOB_ID
research report JOB_ID --retry

curl http://localhost:8000/research/JOB_ID/report
curl -X POST http://localhost:8000/research/JOB_ID/report/retry
```

Report retry invokes only synthesis over retained documents. It does not search,
crawl, embed, or alter the completed ingestion job.

Inspect the exact source behind a query result using its `metadata.document_id`:

```bash
curl http://localhost:8000/documents/DOCUMENT_ID
```

Delete every retained version and derived vector for a source:

```bash
curl -X DELETE "http://localhost:8000/documents?url=https://example.com/page"
```

## Rebuild or re-embed

```bash
docker compose exec research-worker python -m app.rebuild
```

The default destination is separately versioned, for example
`research_corpus__nomic_embed_text_768`; the active collection is not replaced.
Use `--collection NAME` for an explicit destination. Re-running is safe: the
command validates Qdrant points and resumes at the first missing chunk. To activate
a rebuilt index, set `QDRANT_COLLECTION` for both API and worker and recreate them.

Back up the SQLite database (including WAL files) and Qdrant volume together.
`docker compose down -v` deliberately destroys both stores.

An opt-in throughput comparison against the live local Ollama is available with:

```bash
docker run --rm --network hub_default \
  -v "$PWD/research-hub:/work" -w /work \
  -e OLLAMA_URL=http://hub-ollama:11434 hub-research-hub \
  python tests/benchmark_embeddings.py
```
