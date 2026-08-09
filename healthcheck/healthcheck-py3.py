#!/usr/bin/env python3
"""Healthcheck helper - connect to a TCP port to verify the service is listening.
Python-based /dev/tcp replacement for containers with python3 but not bash.
Usage: python3 healthcheck-py3.py <port>
"""
import socket
import sys

if len(sys.argv) < 2:
    print("Usage: healthcheck-py3.py <port>", file=sys.stderr)
    sys.exit(1)

try:
    port = int(sys.argv[1])
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("localhost", port))
    s.close()
    sys.exit(0)
except Exception as e:
    print(f"Failed: {e}", file=sys.stderr)
    sys.exit(1)
