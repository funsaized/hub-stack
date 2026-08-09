FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY app/ ./app/

# Health endpoint (curl handles dual-stack localhost correctly,
# wget on slim images has IPv6/IPv4 issues)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
  CMD curl -f http://localhost:8000/livez || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
