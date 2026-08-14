# Models

Installed in the `hub_ollama_data` volume, ~28 GB total. Listed with
`docker exec hub-ollama ollama list`.

| Model | Size | Measured throughput | Verdict |
|---|---|---|---|
| `qwen3.5:9b` | 6.6 GB | **103.2 tok/s** | The working default |
| `qwen3.6:27b` | 17 GB | **2.8 tok/s** | Installed but effectively unusable |
| `qwen2.5:7b` (= `qwen2.5:latest`) | 4.7 GB | not measured | Two tags, one blob |
| `nomic-embed-text` | 274 MB | n/a | Embedding model, not for chat |

Measured 2026-08-14 on an RTX 3080 Ti (12 GB), driver 610.88, via
`/api/generate` with `stream:false`, reading `eval_count` and `eval_duration`
from the response.

## The VRAM cliff

This card has **12 GB**. A model whose weights exceed free VRAM still runs —
Ollama offloads the overflow to CPU — but the cost is severe and silent:

- `qwen3.5:9b`: ~8.5 GB resident, entirely on the GPU, **103 tok/s**
- `qwen3.6:27b`: VRAM pegged at 12.07 GB with the remainder on CPU, **2.8 tok/s**

That is a **37x** difference. Nothing errors, nothing warns; the request just
takes minutes instead of seconds. Watch the "VRAM used vs total" panel on the
Grafana dashboard — when used approaches total, you are about to pay this.

Rule of thumb for this machine: **stay under ~10 GB of model weights.** That
leaves headroom for the KV cache, which grows with context length.

## Cold start

First request after a model is idle pays a load cost — measured at **31–40 s**
for both models tested. `OLLAMA_KEEP_ALIVE=30m` keeps a model resident that
long after its last request, so only the first question in a session pays it.
Lower it to free VRAM sooner; raise it if you work in long bursts.

## Context length

`OLLAMA_CONTEXT_LENGTH=16384` is offered to every model. Longer contexts
consume VRAM for the KV cache on top of the weights, so raising it moves the
cliff above closer. Raise it and re-check the VRAM panel rather than assuming.

## Managing models

```bash
docker exec hub-ollama ollama list            # what is installed
docker exec hub-ollama ollama pull <model>    # add one
docker exec hub-ollama ollama rm <model>      # remove one
docker exec hub-ollama ollama ps              # what is resident right now
```

Models live in the `hub_ollama_data` volume. It survives `docker compose down`
but **not** `docker compose down -v` — that flag would delete all 28 GB and
require re-pulling everything.
