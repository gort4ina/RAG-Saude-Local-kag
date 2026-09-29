#!/usr/bin/env bash
# Prepara o projeto no Ubuntu/Linux apos copiar de um ambiente Windows.
# - remove venv Windows (Scripts/) e recria .venv
# - reinstala frontend/node_modules com binarios Linux
#
# O backend Docker usa Python 3.12. No host, 3.12/3.13/3.14 funcionam
# (requirements.txt inclui pydantic com wheel para 3.14).
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

echo "==> Projeto: $project_root"

pick_python() {
  local candidate
  for candidate in python3.12 python3.13 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON_BIN="$(pick_python)"; then
  echo "python3 nao encontrado. Instale: sudo apt-get install -y python3 python3-venv python3-pip"
  exit 1
fi

PY_VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "==> Python selecionado: $PYTHON_BIN ($PY_VER)"

if ! command -v npm >/dev/null; then
  echo "npm nao encontrado. Instale Node.js 18+ (nvm ou apt)."
  exit 1
fi

echo "==> Removendo ambientes Windows/legados"
rm -rf .venv .venv-tests

venv_ok=0
echo "==> Criando .venv Linux"
if "$PYTHON_BIN" -m venv .venv; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade pip
  if python -m pip install -r requirements.txt; then
    venv_ok=1
  else
    echo
    echo "Falha ao instalar requirements.txt com $PYTHON_BIN ($PY_VER)."
    echo "Sugestao: use a mesma versao do Docker (3.12):"
    echo "  sudo apt-get install -y python3.12 python3.12-venv"
    echo "  ./scripts/setup-linux.sh"
    echo
    echo "A stack via Docker continua funcionando:"
    echo "  docker compose up -d"
  fi
else
  echo
  echo "Falha ao criar o venv. No Ubuntu instale:"
  echo "  sudo apt-get install -y ${PYTHON_BIN}-venv python3-pip"
  echo "Depois rode de novo: ./scripts/setup-linux.sh"
  echo
  echo "Continuando com a reinstalacao do frontend..."
  rm -rf .venv
fi

if [ -d "frontend/node_modules/@esbuild/linux-x64" ]; then
  echo "==> frontend/node_modules ja e Linux — mantido"
else
  echo "==> Reinstalando frontend/node_modules (binarios Linux)"
  rm -rf frontend/node_modules frontend/.angular/cache
  (
    cd frontend
    if [ -f package-lock.json ]; then
      npm ci
    else
      npm install
    fi
  )
fi

chmod +x scripts/*.sh 2>/dev/null || true

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> .env criado a partir de .env.example — troque as senhas antes de subir"
else
  echo "==> .env ja existe (mantido)"
fi

echo
if [ "$venv_ok" -eq 1 ]; then
  echo "Pronto."
else
  echo "Setup parcial. Use Docker para a stack completa se o venv falhou."
fi
echo "  Stack Docker (CPU):  docker compose up -d"
echo "  Stack Docker (GPU):  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d"
echo "  Dev local:           ./scripts/start.sh"
echo "  Testes:              .venv/bin/python -m pytest -q"
