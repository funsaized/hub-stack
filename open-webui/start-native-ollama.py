import os

# Host networking lets WebUI use the same loopback proxy as local agents.
# Never point this at :11435: doing so bypasses invocation history and metrics.
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
os.execv("/usr/bin/bash", ["bash", "start.sh"])
