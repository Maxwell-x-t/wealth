#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

if [ ! -d "$BACKEND_DIR/.venv" ]; then
  python3 -m venv "$BACKEND_DIR/.venv"
fi
if ! "$BACKEND_DIR/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
  "$BACKEND_DIR/.venv/bin/python" -m ensurepip
fi
(cd "$BACKEND_DIR" && .venv/bin/python -m pip install -r requirements.txt)

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  (cd "$FRONTEND_DIR" && npm install)
fi

available_port() {
  "$BACKEND_DIR/.venv/bin/python" -c '
import socket, sys
port = int(sys.argv[1])
while port < 65535:
    try:
        with socket.socket() as server:
            server.bind(("127.0.0.1", port))
        print(port)
        break
    except OSError:
        port += 1
else:
    raise SystemExit("No available local port")
' "$1"
}
BACKEND_PORT=$(available_port "${WEALTH_BACKEND_PORT:-8000}")
FRONTEND_PORT=$(available_port "${WEALTH_FRONTEND_PORT:-5173}")
export WEALTH_API_URL="http://127.0.0.1:$BACKEND_PORT"

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT

(
  cd "$BACKEND_DIR"
  exec .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT"
) &
BACKEND_PID=$!

(
  cd "$FRONTEND_DIR"
  exec npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort
) &
FRONTEND_PID=$!

echo "后端: http://127.0.0.1:$BACKEND_PORT"
echo "策略账户: http://127.0.0.1:$FRONTEND_PORT/strategy"
wait
