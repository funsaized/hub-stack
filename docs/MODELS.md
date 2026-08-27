# Models

Models are stored by native Ollama under `/var/lib/ollama` and listed with
`ollama list`.

| Model | Quantization | Size | Verdict |
|---|---|---|---|
| `qwen3.5:9b` | Q4_K_M | 6.6 GB | Selected local chat model |

## Why this model

The Ollama catalog checked on 2026-08-25 lists Qwen 3.8 as the newest release,
but only at 27B/18 GB. `qwen3.5:9b` has 9.65B parameters, text and image input,
tool support, and a 256K model limit. The stack deliberately serves only 8K
context because the runtime context, not the model's theoretical limit,
determines KV-cache VRAM.

The older `qwen3:8b` model was also considered. It is 5.2 GB and fits, but
`qwen3.5:9b` provides the newer architecture and multimodal support while still
fitting. Larger candidates are poor matches:

| Candidate | Ollama size | Reason not selected |
|---|---:|---|
| `qwen3:8b` | 5.2 GB | Older generation; less capability headroom |
| `qwen3:14b` | 9.3 GB | Marginal once KV cache and the desktop use VRAM |
| `qwen3.5:9b-q8_0` | 11 GB | Weights alone nearly consume available VRAM |
| `qwen3.8:27b` | 18 GB | Newest official model, but must spill to CPU |

## The VRAM cliff

This card has **12 GB**. A model whose weights exceed free VRAM still runs —
Ollama offloads the overflow to CPU — but the cost is severe and silent:

- `qwen3.5:9b`: about 7.13 GiB peak card use and **111 tok/s wall decode** on Linux
- A 17 GB 27B model: historically **2.8 tok/s** after spilling to CPU

The 27B result is historical and not a controlled cross-platform comparison,
but it demonstrates the scale of the penalty. Nothing errors or warns; the
request just takes minutes instead of seconds. Watch the "VRAM used vs total"
panel on the Grafana dashboard before trying a larger model.

Rule of thumb for this machine: **stay under ~10 GB of model weights.** That
leaves headroom for the KV cache, which grows with context length.

## Cold start

First request after a true unload takes about **3.1 s**, or about **2.6 s** if
the GGUF remains in the host page cache; `llama-server` starts in about 1.8 s.
`OLLAMA_KEEP_ALIVE=30m` keeps the model resident that long after its last
request. Lower it to free VRAM sooner; raise it if you work in long bursts.

The unit requests `OLLAMA_NUM_PARALLEL=2` for future dense models, but Ollama
0.32.15 forces this model's hybrid `qwen35` architecture to `-np 1`. Concurrent
requests serialize even though enough VRAM remains for another context.

## Context length

`OLLAMA_CONTEXT_LENGTH=8192` is offered to every model and
`OLLAMA_FLASH_ATTENTION=1` reduces attention memory use. Longer contexts
consume VRAM for the KV cache on top of the weights, so raising it moves the
cliff closer. Raise it and re-check the VRAM panel rather than assuming.

## Managing models

```bash
ollama list            # what is installed
ollama pull <model>    # add one
ollama rm <model>      # remove one
ollama ps              # what is resident right now
```

Models are independent of Docker lifecycle commands. `docker compose down -v`
removes UI and monitoring data but does not remove native Ollama models.

After any model or context change, generate one response and check the actual
offload instead of relying on model size:

```bash
ollama ps
```

The `PROCESSOR` column must say `100% GPU` for the intended configuration.
