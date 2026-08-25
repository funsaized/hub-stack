import os
import socket
import struct


with open("/proc/net/route", encoding="ascii") as routes:
    rows = (line.split() for line in routes)
    gateway_hex = next((row[2] for row in rows if row[1] == "00000000"), None)

if gateway_hex is None:
    raise RuntimeError("cannot resolve the Docker bridge gateway")

gateway = socket.inet_ntoa(struct.pack("<L", int(gateway_hex, 16)))
os.environ["OLLAMA_BASE_URL"] = f"http://{gateway}:11434"
os.execv("/usr/bin/bash", ["bash", "start.sh"])
