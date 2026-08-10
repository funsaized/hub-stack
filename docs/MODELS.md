# Models

## Current

Measured on the RTX 3080 Ti host at an 8,192-token operating context. Generation
rates are Ollama's warm, long-form server rates; they exclude a separate localhost
response-path delay observed during the benchmark.

| Model | Disk size | Role | Warm generation | Placement at 8K | Notes |
|---|---:|---|---:|---|---|
| `qwen3.5:9b` (Q4_K_M) | 6.6 GB | Default / fast fallback | 102.49 tok/s | 100% GPU | 9.7B, vision/tools, exact OCR result |
| `qwen3.6:27b` (Q4_K_M) | 17 GB | Explicit high-quality/offline jobs | 3.35 tok/s | 45% CPU / 55% GPU | 27.8B, below the 8 tok/s interactive gate |
| `qwen2.5:7b` | 4.7 GB | Retained benchmark baseline | 124.85 tok/s | 100% GPU | Existing tag retained unchanged |
| `nomic-embed-text` | 274 MB | Embeddings | n/a | GPU when queried | 768 dimensions; collection unchanged |

Research Hub startup explicitly ensures only `nomic-embed-text`. Generation models
must be pulled separately before selecting them. All four models are currently stored
in the persistent Ollama volume.

## Selection decision

`qwen3.6:27b` remained stable but failed the deployment gate: 3.35-4.12 generated
tokens/s, non-zero swap after the large-model run, low Windows memory headroom, and
an OCR error. It remains installed for deliberate high-quality jobs. `qwen3.5:9b`
is the default because it stayed fully on the GPU, produced valid JSON/tool calls,
read the OCR fixture exactly, and generated at about 102 tokens/s.

The operating context remains 8K. The 16K/32K tuning pass was skipped because
Qwen3.6 did not pass at 8K. Advertised 262K contexts are architectural maxima, not
appropriate operating targets for this 32 GB host.

## Swapping models

### Use a different generation model

`LLM_MODEL` controls Research Hub, Research Worker, and Crawl4AI together. In
PowerShell, switch all three without editing Compose:

```powershell
# The model must already be present in the Ollama volume.
docker exec hub-ollama ollama pull qwen3.6:27b

$env:LLM_MODEL = "qwen3.6:27b"
docker compose up -d --force-recreate crawl4ai research-hub research-worker

# Return this PowerShell session to the measured default.
$env:LLM_MODEL = "qwen3.5:9b"
docker compose up -d --force-recreate crawl4ai research-hub research-worker
```

For a persistent local override, set `LLM_MODEL` in `.env`; `.env.example` documents
the measured default. Do not assign different defaults to Crawl4AI and Research Hub,
because that causes repeated model swapping.

### Use a different embedding model

Same pattern, but you also need to update the Qdrant collection dimension:

```bash
# Pick a model
# nomic-embed-text: 768
# mxbai-embed-large: 1024
# snowflake-arctic-embed: 1024
# all-MiniLM-L6-v2: 384

# Update research-hub env
#   EMBEDDING_MODEL=mxbai-embed-large
#   EMBEDDING_DIMENSION=1024

# Use a new QDRANT_COLLECTION or explicitly migrate and re-embed retained data.
# research-hub will refuse to start against an incompatible existing collection.
```

Do not delete the existing collection as an automatic model-switch step. On startup, research-hub compares the existing collection's vector size and cosine distance with `EMBEDDING_DIMENSION`. A mismatch produces a clear migration error without modifying retained points.

## Storing more models

Models stored on disk consume no VRAM until loaded. The stack deliberately uses:

```dotenv
OLLAMA_KEEP_ALIVE=30m
OLLAMA_NUM_PARALLEL=1
OLLAMA_MAX_LOADED_MODELS=1
```

Only one model and one request are active at a time. This prevents VRAM overcommit
and concurrent generation thrashing. RAG requests may sequentially swap the embedding
model and the configured generation model; `ollama ps` must never show more than one.

At 8K, Qwen3.5 used about 7,691 MiB total GPU memory in the benchmark. Qwen3.6
used about 11,358 MiB and split its 17 GB runner 45% CPU / 55% GPU.

## Model selection logic

The same `LLM_MODEL` is supplied to Research Hub, Research Worker, and Crawl4AI.
The measured default is `qwen3.5:9b`; Qwen3.6 is selected only by explicit override.

## How to verify what you have

```powershell
# List pulled models
docker exec hub-ollama ollama list

# Show model details
docker exec hub-ollama ollama show qwen3.5:9b

# Pull a new model
docker exec hub-ollama ollama pull <model>
```

## WSL memory envelope

`C:\Users\saigu\.wslconfig` limits WSL to 20 GB RAM, 12 logical processors,
and 2 GB swap. `vmIdleTimeout=-1` and `autoMemoryReclaim=dropCache` remain enabled.
Changes to this file require a brief restart:

```powershell
wsl --shutdown
wsl -d Ubuntu
```

Docker Desktop integration may take a few minutes to reconnect. Do not start the
`webui` Compose profile while applying model changes.

## Future

- **Dynamic model selection**: pick the right model for the right task
- **Model routing**: use cloud APIs for hard tasks, local for easy ones
- **Speculative decoding**: small model drafts, large model verifies
- **Quantisation experiments**: try Q4_K_S vs Q4_K_M vs Q5_K_M vs Q8_0
