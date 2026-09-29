"""Remove um documento da colecao vetorial pela linha de comando.

Ferramenta de contingência para administradores com acesso ao container. A
rota HTTP autenticada deve ser preferida porque também gera auditoria.

Uso (a partir da raiz do projeto, com a stack no ar):

    docker compose exec api python -m scripts.remove_document --tenant <tenant_id> --list
    docker compose exec api python -m scripts.remove_document --tenant <tenant_id> --id <document_id>

O indice BM25 e reconstruido na proxima inicializacao da API, entao reinicie
o servico depois de remover:

    docker compose restart api
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.services.vector_store import VectorStore


def _store() -> VectorStore:
    settings = get_settings()
    return VectorStore(
        path=settings.chroma_path,
        collection_name=settings.collection_name,
        embedding_model=settings.embedding_model,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove documentos da colecao.")
    parser.add_argument("--tenant", required=True, help="tenant_id proprietário dos dados.")
    parser.add_argument("--list", action="store_true", help="Lista os documentos.")
    parser.add_argument("--id", dest="document_id", help="document_id a remover.")
    parser.add_argument(
        "--yes", action="store_true", help="Nao pedir confirmacao interativa."
    )
    args = parser.parse_args()

    store = _store()
    documents = store.list_documents(args.tenant)

    if args.list or not args.document_id:
        if not documents:
            print("A colecao esta vazia.")
            return 0
        print(f"Colecao: {get_settings().collection_name}\n")
        for item in documents:
            print(
                f"  {item['document_id']}  {item['filename']}  "
                f"({item['chunks']} trecho(s))"
            )
        if not args.document_id:
            return 0

    alvo = next(
        (item for item in documents if item["document_id"] == args.document_id), None
    )
    if alvo is None:
        print(f"Nenhum documento com id {args.document_id}.", file=sys.stderr)
        return 1

    if not args.yes:
        resposta = input(
            f"\nRemover '{alvo['filename']}' ({alvo['chunks']} trechos)? [s/N] "
        )
        if resposta.strip().lower() not in {"s", "sim", "y", "yes"}:
            print("Cancelado.")
            return 1

    store.delete_document(args.tenant, args.document_id)
    print(f"Removido: {alvo['filename']}")
    print("Rode 'docker compose restart api' para reconstruir o indice BM25.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
