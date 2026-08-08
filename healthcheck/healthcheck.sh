#!/bin/sh
# Generic healthcheck helper - tries curl first, falls back to python, then to /dev/tcp
# Usage: healthcheck.sh <port>
PORT="${1:-}"
if [ -z "$PORT" ]; then
  echo "Usage: $0 <port>" >&2
  exit 1
fi
# Try curl
if command -v curl >/dev/null 2>&1; then
  curl -fs -o /dev/null "http://localhost:${PORT}/" && exit 0
  curl -fs -o /dev/null "http://localhost:${PORT}/health" && exit 0
  exit 1
fi
# Try python3
if command -v python3 >/dev/null 2>&1; then
  python3 -c "
import socket, sys
try:
    s = socket.socket()
    s.settimeout(3)
    s.connect(('localhost', int('${PORT}')))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" && exit 0
fi
# Try bash /dev/tcp
if [ -n "$BASH_VERSION" ]; then
  exec 3<>/dev/tcp/localhost/"$PORT" 2>/dev/null && exit 0 || exit 1
fi
exit 1
