FROM python:3.12-slim

WORKDIR /app

ENV HF_HOME=/opt/huggingface

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY app/ ./app/

# Bake the immutable verifier snapshot into the image; runtime is offline-only.
RUN python -c "from transformers import AutoModelForSequenceClassification, AutoTokenizer; m='MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli'; r='6f5cf0a2b59cabb106aca4c287eed12e357e90eb'; AutoTokenizer.from_pretrained(m, revision=r); AutoModelForSequenceClassification.from_pretrained(m, revision=r)"

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false

# Health endpoint (curl handles dual-stack localhost correctly,
# wget on slim images has IPv6/IPv4 issues)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
  CMD curl -f http://localhost:8000/livez || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
