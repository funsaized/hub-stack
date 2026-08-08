"""Test the research-hub end-to-end. Runs inside WSL."""
import json
import time
import sys
import urllib.request

BASE = "http://localhost:8000"

def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
        return json.loads(r.read())

# 1. Submit a job
job_payload = {
    "topic": "best practices for running local LLMs on consumer hardware",
    "depth": 3,
    "max_sources": 5,
    "tags": ["llm", "local-hardware"]
}

print("=== Submitting research job ===")
job = post("/research", job_payload)
job_id = job["job_id"]
print(f"Job ID: {job_id}")
print(f"Initial status: {job['status']}")

# 2. Poll
print("\n=== Polling status ===")
for i in range(30):
    time.sleep(15)
    job = get(f"/research/{job_id}")
    status = job["status"]
    progress = job.get("progress", {})
    print(f"[{i}] {status} - {progress}")
    if status in ("completed", "failed"):
        break

print("\n=== Final job ===")
print(json.dumps(job, indent=2))
