"""Gera um ``.env`` com segredos novos, a partir de um arquivo de exemplo.

Uso (na raiz do projeto)::

    python scripts/gen_env.py                 # desenvolvimento (.env.example)
    python scripts/gen_env.py --production    # producao (.env.production.example)

O que faz:

- troca ``POSTGRES_PASSWORD``, ``JWT_SECRET`` e ``BOOTSTRAP_ADMIN_PASSWORD`` por
  valores aleatorios (``secrets.token_urlsafe``) e atualiza a senha dentro de
  ``DATABASE_URL``, quando ela existir no exemplo;
- recusa sobrescrever um ``.env`` existente (use ``--force`` conscientemente);
- no Linux/macOS grava o arquivo com permissao 0600;
- **nao imprime nenhum segredo**. Para ler a senha do admin, abra o ``.env``.

So usa a biblioteca padrao.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Chave -> numero de bytes aleatorios (token_urlsafe devolve ~1,3 caractere
# por byte). 48 bytes dao 64 caracteres, bem acima do minimo de 32 do JWT.
GENERATED: dict[str, int] = {
    "POSTGRES_PASSWORD": 32,
    "JWT_SECRET": 48,
    "BOOTSTRAP_ADMIN_PASSWORD": 18,
}

_DATABASE_URL = re.compile(r"^(DATABASE_URL=[^:\n]+://[^:\n]+:)([^@\n]*)(@.*)$")


def render_env(template: str) -> tuple[str, list[str]]:
    """Devolve o conteudo do ``.env`` e as chaves que receberam segredo novo."""
    values = {key: secrets.token_urlsafe(size) for key, size in GENERATED.items()}
    done: list[str] = []
    lines: list[str] = []

    for line in template.splitlines():
        stripped = line.strip()
        replaced = False
        for key, value in values.items():
            if stripped.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                done.append(key)
                replaced = True
                break
        if replaced:
            continue
        match = _DATABASE_URL.match(line)
        if match:
            line = f"{match.group(1)}{values['POSTGRES_PASSWORD']}{match.group(3)}"
        lines.append(line)

    return "\n".join(lines) + "\n", done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera .env com segredos novos.")
    parser.add_argument(
        "--production",
        action="store_true",
        help="usa .env.production.example como base",
    )
    parser.add_argument(
        "--output", default=".env", help="arquivo de saida (padrao: .env)"
    )
    parser.add_argument(
        "--force", action="store_true", help="sobrescreve o arquivo de saida"
    )
    args = parser.parse_args(argv)

    template_name = ".env.production.example" if args.production else ".env.example"
    template_path = ROOT / template_name
    output_path = (ROOT / args.output).resolve()

    if output_path.exists() and not args.force:
        print(
            f"Recusado: {output_path.name} ja existe. Apague ou use --force "
            "(isso troca TODOS os segredos e invalida as sessoes abertas).",
            file=sys.stderr,
        )
        return 1

    content, generated = render_env(template_path.read_text(encoding="utf-8"))
    missing = set(GENERATED) - set(generated)
    if missing:
        print(
            f"{template_name} nao tem as chaves: {', '.join(sorted(missing))}",
            file=sys.stderr,
        )
        return 2

    output_path.write_text(content, encoding="utf-8", newline="\n")
    if os.name == "posix":
        output_path.chmod(0o600)

    print(f"Criado {output_path.name} a partir de {template_name}.")
    print("Segredos gerados: " + ", ".join(generated))
    print(
        "A senha do admin esta em BOOTSTRAP_ADMIN_PASSWORD dentro do arquivo "
        "(nao e exibida aqui). O .env esta no .gitignore: nao o commite."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
