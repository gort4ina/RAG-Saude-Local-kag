#!/usr/bin/env bash
# Sobe API (uvicorn) + frontend Angular em modo desenvolvimento local.
# Preferivel no Ubuntu: ./scripts/start.sh
# Para a stack completa (Postgres, Ollama, nginx): use docker compose.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [ ! -d ".venv" ] || [ ! -x ".venv/bin/python" ]; then
  echo "Ambiente .venv ausente ou invalido (ex.: venv criado no Windows)."
  echo "Rode: ./scripts/setup-linux.sh"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload &
api_pid=$!

cleanup() {
  kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT

cd frontend
if [ ! -d "node_modules" ] || [ -d "node_modules/@esbuild/win32-x64" ]; then
  echo "Reinstalando node_modules para Linux..."
  rm -rf node_modules
  npm ci
fi
npm start
