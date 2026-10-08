"""Ingere os exemplos didaticos de ``knowledge_base/`` no tenant alvo.

Objetivo
--------
A suite de avaliacao (``python -m eval.run_eval``) precisa de um acervo real
indexado para que ``recall_at_5`` deixe de ser ``None``. Este script faz a
preparacao de base, idempotente, usando a API HTTP autenticada (ou seja,
exercita o mesmo caminho que um usuario real).

Fluxo
-----
1. Faz login via ``POST /api/auth/token`` (OAuth2 password flow).
2. Para cada ``*.md`` ou ``*.pdf`` em ``knowledge_base/``:
   a. Copia o sidecar ``<arquivo>.meta.json`` (se existir) para
      ``UPLOAD_PATH`` com o nome que o backend procura. Isso permite marcar
      a vigencia como ``vigente_confirmada`` sem passar pela UI de admin.
   b. Faz ``POST /api/documents/upload`` com ``multipart/form-data``.
   c. Trata ``409 document_already_exists`` como OK (ja indexado).

Uso tipico (com a stack rodando):

    docker compose exec api python -m scripts.seed_knowledge_base \
        --base-url http://api:8000 \
        --tenant local --user admin --password "$BOOTSTRAP_ADMIN_PASSWORD"

Fora do container:

    python -m scripts.seed_knowledge_base \
        --base-url http://localhost:8080 \
        --tenant local --user admin --password "$BOOTSTRAP_ADMIN_PASSWORD" \
        --upload-path ./data/uploads

``--upload-path`` so eh necessario para copiar os sidecars antes do upload. Se
omitido, o script le ``UPLOAD_PATH`` do ambiente (fallback: ``./data/uploads``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "knowledge_base"
SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}


@dataclass(frozen=True)
class SeedResult:
    filename: str
    status: str  # "indexed" | "already_exists" | "skipped" | "error"
    detail: str = ""


def _collect_documents() -> list[Path]:
    files: list[Path] = []
    for path in sorted(KB_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
    return files


def _copy_sidecar(doc_path: Path, upload_dir: Path) -> bool:
    sidecar = doc_path.with_suffix(doc_path.suffix + ".meta.json")
    if not sidecar.is_file():
        return False
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / sidecar.name
    shutil.copyfile(sidecar, target)
    return True


def _login(client: httpx.Client, tenant: str, user: str, password: str) -> str:
    response = client.post(
        "/api/auth/token",
        data={
            "username": f"{tenant}/{user}",
            "password": password,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Login falhou ({response.status_code}): {response.text[:300]}"
        )
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Resposta de login sem 'access_token'.")
    return str(token)


def _upload(client: httpx.Client, path: Path) -> SeedResult:
    mime = {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".pdf": "application/pdf",
    }.get(path.suffix.lower(), "application/octet-stream")
    with path.open("rb") as handle:
        files = {"file": (path.name, handle, mime)}
        response = client.post("/api/documents/upload", files=files)
    if response.status_code == 200:
        payload = response.json()
        return SeedResult(
            filename=path.name,
            status="indexed",
            detail=f"{payload.get('chunks', 0)} chunk(s), doc={payload.get('document_id')}",
        )
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = {}
    code = str(payload.get("code") or f"http_{response.status_code}")
    if code == "document_already_exists":
        return SeedResult(filename=path.name, status="already_exists", detail=code)
    return SeedResult(
        filename=path.name,
        status="error",
        detail=f"{code}: {payload.get('detail') or response.text[:200]}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--tenant", default=os.getenv("BOOTSTRAP_TENANT_SLUG", "local"))
    parser.add_argument("--user", default=os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"))
    parser.add_argument(
        "--password",
        default=os.getenv("BOOTSTRAP_ADMIN_PASSWORD", ""),
        help="Senha do admin de bootstrap. Obrigatoria.",
    )
    parser.add_argument(
        "--upload-path",
        type=Path,
        default=Path(os.getenv("UPLOAD_PATH", str(ROOT / "data" / "uploads"))),
        help="Diretorio UPLOAD_PATH para copiar os sidecars .meta.json.",
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)

    if not args.password:
        print(
            "Senha nao informada. Use --password ou defina BOOTSTRAP_ADMIN_PASSWORD.",
            file=sys.stderr,
        )
        return 2

    if not KB_DIR.is_dir():
        print(f"Diretorio nao encontrado: {KB_DIR}", file=sys.stderr)
        return 2

    documents = _collect_documents()
    if not documents:
        print("Nenhum documento suportado em knowledge_base/.", file=sys.stderr)
        return 1

    print(
        f"Base: {args.base_url} | tenant={args.tenant} user={args.user} "
        f"| {len(documents)} arquivo(s) | upload_path={args.upload_path}\n"
    )

    results: list[SeedResult] = []
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        token = _login(client, args.tenant, args.user, args.password)
        client.headers["Authorization"] = f"Bearer {token}"
        for doc in documents:
            sidecar_copied = _copy_sidecar(doc, args.upload_path)
            result = _upload(client, doc)
            prefix = "ok " if result.status in {"indexed", "already_exists"} else "ERR"
            sidecar_tag = " [sidecar]" if sidecar_copied else ""
            print(f"[{prefix}] {result.filename:<48}{sidecar_tag} {result.status} {result.detail}")
            results.append(result)

    errors = [r for r in results if r.status == "error"]
    indexed = [r for r in results if r.status == "indexed"]
    already = [r for r in results if r.status == "already_exists"]
    print(
        f"\nResumo: {len(indexed)} indexado(s), {len(already)} ja existia(m), "
        f"{len(errors)} erro(s)."
    )
    return 1 if errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
