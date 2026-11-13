#!/usr/bin/env bash
# Start the whole NONO stack for local use. Expects the three repos side by side:
#   ai_jarvis_assistant_backend/  ai_jarvis_assistant_rag_agent/  ai_jarvis_assistant_frontend/
# (or folders named backend/ rag_agent/ frontend/)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(dirname "$HERE")"
find_dir() { for d in "$ROOT/$1" "$ROOT/ai_jarvis_assistant_$1"; do [ -d "$d" ] && { echo "$d"; return; }; done; echo ""; }
RAG="$(find_dir rag_agent)"; FE="$(find_dir frontend)"; BE="$HERE"
[ -z "$RAG" ] && { echo "rag_agent folder not found next to backend"; exit 1; }
[ -z "$FE" ] && { echo "frontend folder not found next to backend"; exit 1; }

setup_py() { [ -d "$1/.venv" ] || { (cd "$1" && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt); }; [ -f "$1/.env" ] || cp "$1/.env.example" "$1/.env"; }
setup_py "$RAG"; setup_py "$BE"
[ -d "$FE/node_modules" ] || (cd "$FE" && npm install)

trap 'kill 0' EXIT
(cd "$RAG" && .venv/bin/python -m nono_rag) &
sleep 2
(cd "$BE" && .venv/bin/python -m nono) &
sleep 2
(cd "$FE" && npm run dev) &
echo ""
echo "  NONO is starting →  http://localhost:5173   (Ctrl+C to stop everything)"
wait
