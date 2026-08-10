#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/.venv/bin/python3" "$ROOT/src/current_state_gate.py" require --action service_status

python - <<'PY'
import socket
for name, port in [('neo4j_http', 7474), ('neo4j_bolt', 7687), ('embedding_openai', 8010)]:
    sock = socket.socket()
    sock.settimeout(0.5)
    try:
        sock.connect(('127.0.0.1', port))
        status = 'open'
    except OSError:
        status = 'closed'
    finally:
        sock.close()
    print(f'{name} {port} {status}')
PY
