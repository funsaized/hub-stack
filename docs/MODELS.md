# Models

## Current

| Model | Size | VRAM | Purpose | Notes |
|---|---|---|---|---|
| `qwen2.5:7b` (Q4_K_M) | 4.7 GB | ~5 GB | Generation | Good quality/speed fit for 12 GB VRAM; multilingual |
| `nomic-embed-text` | 274 MB | ~0.5 GB | Embeddings | 768-dim, strong retrieval benchmark |

Both are pulled automatically by the research-hub on first startup (via `ensure_embedding_model`).

## Why these models

**qwen2.5:7b**
- Beats Llama 3 8B and Mistral 7B on most reasoning benchmarks
- 128K context window (we use 8K)
- Fast on RTX 3080 Ti (~40 tok/s generation)
- Good at structured output (we use it for citation formatting)

**nomic-embed-text**
- 768-dim (matches Qdrant collection size)
- Beats OpenAI text-embedding-3-small on retrieval at this size
- 8K context window
- ~50ms per chunk on this hardware

## Swapping models

### Use a different generation model

Edit `docker-compose.yml`, find the Ollama service, change `OLLAMA_KEEP_ALIVE` if needed, then:

```bash
# Pull the new model
docker exec hub-ollama ollama pull <model-name>

# Update research-hub's env
# In docker-compose.yml, the research-hub service:
#   - LLM_MODEL=<model-name>

# Restart
docker compose up -d --force-recreate research-hub
```

Common swaps:

- **Higher quality, slower**: `qwen2.5:14b` (8-9 GB VRAM, ~2x slower)
- **Faster, weaker**: `llama3.2:3b` (2 GB VRAM, ~70 tok/s)
- **Best local, biggest**: `qwen2.5:32b` (Q4_K_M, ~20 GB — won't fit on 12 GB, needs CPU offload)
- **Code-focused**: `qwen2.5-coder:7b`

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

Each model takes GPU memory. Storing many models on disk is cheap (just disk space), but loading them into VRAM is the constraint.

Current VRAM budget on RTX 3080 Ti (12 GB):
- qwen2.5:7b loaded: ~5 GB
- nomic-embed-text loaded: ~0.5 GB
- Headroom for context: ~6 GB

You can have other models pulled but not loaded. They'll load on demand if you call them.

## Model selection logic

Currently the agent picks one model and uses it for everything. A future improvement: pick the best model per task (e.g., a small model for classification, a large one for synthesis).

## How to verify what you have

```bash
# List pulled models
docker exec hub-ollama ollama list

# Show model details
docker exec hub-ollama ollama show qwen2.5:7b

# Pull a new model
docker exec hub-ollama ollama pull <model>
```

## Pre-pulling models at deploy time

If you want a specific model to be available the first time the stack starts, add to `research-hub/app/main.py`:

```python
# In the lifespan startup, after ensure_embedding_model:
async with httpx.AsyncClient(timeout=600) as client:
    await client.post(f"{cfg.ollama_url}/api/pull", json={"name": "qwen2.5:7b"})
```

Or as a one-shot:

```bash
docker exec hub-ollama ollama pull qwen2.5:7b
```

## Future

- **Dynamic model selection**: pick the right model for the right task
- **Model routing**: use cloud APIs for hard tasks, local for easy ones
- **Speculative decoding**: small model drafts, large model verifies
- **Quantisation experiments**: try Q4_K_S vs Q4_K_M vs Q5_K_M vs Q8_0
