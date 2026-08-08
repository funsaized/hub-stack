"""Test query and RAG endpoints."""
import json
import urllib.request

BASE = "http://localhost:8000"

def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())

print("=== Query test ===")
q = post("/query", {"query": "how to run a local LLM on consumer hardware", "top_k": 3})
print(f"Query: {q['query']}")
print(f"Got {len(q['chunks'])} chunks")
for i, c in enumerate(q['chunks'], 1):
    title = c['source_title']
    score = c['score']
    text = c['text'][:200]
    print(f"[{i}] {title} (score={score:.3f})")
    print(f"    {text}...")
    print()

print("=== RAG test ===")
r = post("/rag", {"query": "what are the best practices for running local LLMs?", "top_k": 3})
print(f"Answer ({r['model']}):")
print(r['answer'])
print(f"\nSources: {len(r['sources'])}")
for i, c in enumerate(r['sources'], 1):
    print(f"  [{i}] {c['source_title']} - {c['source_url']}")
