FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps from the hashed transitive lockfile (HUB-012).
# Regenerate with scripts/relock.ps1 after editing requirements.txt.
# HUB-034 removed the baked DeBERTa snapshot and the torch/transformers
# stack: the claim gate is the MiniMax judge (app/judge_gate.py).
COPY requirements.lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt

# Copy app
COPY app/ ./app/

# Health endpoint (curl handles dual-stack localhost correctly,
# wget on slim images has IPv6/IPv4 issues)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
  CMD curl -f http://localhost:8000/livez || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
