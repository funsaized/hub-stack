# Application observability

Research-Hub emits single-line JSON logs and Prometheus metrics. Request bodies,
topics, prompts, authorization headers, and crawler credentials are deliberately
excluded from logs and metric labels.

## Correlation and logs

Every API response includes `X-Request-ID`. A caller-supplied value is preserved
when it is 1–128 characters; otherwise the API creates a UUID. The submitted job
ID becomes the worker correlation ID, so `job_submitted`, `job_started`, phase,
source crawl, retry, and terminal events can be followed across the API and worker.

Structured records contain the applicable `job_id`, `phase`, source URL/domain,
duration, retry count, and failure category. Inspect them in Dozzle or with:

```bash
docker compose logs research-hub research-worker
```

## Metrics and dashboard

Start this optional stack with `docker compose --profile observability up -d`.
The API exposes `/metrics`; the worker exposes port 9000 only on the Compose
network. Prometheus scrapes both every 15 seconds. Grafana is available at
http://localhost:3002 with the provisioned **Research Hub Pipeline** dashboard.
Prometheus and its alert status are available at http://localhost:9090.

Metrics cover HTTP rate/latency, job outcomes and phase latency, search-result
counts, crawl outcomes, chunks per source, embedding/upsert latency, retrieval
scores, generation latency, and estimated generation tokens. Labels are bounded;
URLs and domains appear only in logs, preventing unbounded metric cardinality.

Report synthesis additionally exposes bounded verifier outcomes and latency plus correction
outcomes. Outcome labels contain only fixed reason codes such as `entailment`, `neutral`,
`contradiction`, `low_confidence`, `over_budget`, `timeout`, and `revision_mismatch`; claim
text, topics, URLs, job IDs, and evidence IDs are never metric labels.

Provisioned warning thresholds are:

- any terminal job failure in 15 minutes;
- API 5xx rate above 5% for 10 minutes;
- RAG generation p95 above 60 seconds for 15 minutes.

Prometheus evaluates these rules locally. Configure an Alertmanager receiver if
notifications beyond the Prometheus UI are required.
