#!/usr/bin/env bash
# Gera um ZIP limpo do projeto, sem dependencias, caches, vetores ou segredos.
#
# O script NUNCA apaga nada do diretorio de trabalho: ele copia os arquivos
# permitidos para uma pasta temporaria e compacta essa copia.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_name="$(basename "$project_root")"
output_path="${1:-$(dirname "$project_root")/${project_name}-clean-$(date +%Y%m%d-%H%M%S).zip}"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
mkdir -p "$staging/$project_name"

echo "Origem : $project_root"
echo "Staging: $staging/$project_name"
echo

# Modelos do Ollama vivem no volume Docker `ollama_data`, fora do projeto.
copied=$(cd "$project_root" && find . -type f \
  -not -path '*/.venv/*' \
  -not -path '*/venv/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/.angular/*' \
  -not -path '*/dist/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/.pytest_cache/*' \
  -not -path '*/.ruff_cache/*' \
  -not -path '*/.mypy_cache/*' \
  -not -path '*/.git/*' \
  -not -path '*/data/*' \
  -not -path '*/htmlcov/*' \
  -not -name '.env' \
  -not -name '.coverage' \
  -not -name '*.zip' \
  -not -name '*.pyc' \
  -not -name '*.gguf' \
  -not -name '*.bin' \
  -not -name '*.safetensors' \
  -not -name '*.sqlite3' \
  -print0 | tee >(tr -dc '\0' | wc -c >"$staging/.count") \
  | xargs -0 -I{} cp --parents "{}" "$staging/$project_name/" 2>/dev/null; cat "$staging/.count")

rm -f "$staging/.count"
rm -f "$output_path"
(cd "$staging" && zip -qr "$output_path" "$project_name")

echo "Arquivos copiados: $copied"
echo
echo "ZIP gerado: $output_path ($(du -h "$output_path" | cut -f1))"
echo 'Nenhum arquivo do projeto original foi alterado ou removido.'
