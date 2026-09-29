"""Contrato mínimo de um vetor store, base para trocar ChromaDB por pgvector.

O ``RagService`` só usa o vetor store através dos métodos listados aqui.
Documentar esse subset como um ``Protocol`` explícito serve para dois fins:

1. **Preparar a migração para pgvector** sem quebrar o pipeline: basta
   escrever um ``PgVectorStore`` que implemente as mesmas assinaturas e a
   troca vira uma questão de configuração (``dependencies.get_rag_service``
   escolhe qual implementação instanciar).

2. **Facilitar os testes:** a suíte já usa dublês (``FakeStore``) que
   satisfazem exatamente esse contrato — agora ele fica documentado.

Não implementamos aqui o backend de pgvector. A troca em produção depende
de decisão operacional (uma instância Postgres vs. duas persistências
distintas). O plano operacional está em ``docs/PGVECTOR-PLANO.md``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class VectorStorePort(Protocol):
    """Superfície pública consumida pelo ``RagService``.

    Todos os métodos são **isolados por tenant** — o parâmetro ``tenant_id``
    é obrigatório e o backend precisa aplicá-lo em toda leitura/escrita.
    """

    def health(self) -> bool:
        """Ping barato do backend. Não deve levantar exceções."""

    def count_chunks(self, tenant_id: str) -> int:
        """Total de chunks indexados para o tenant. Usado para early-exit."""

    def query(
        self, tenant_id: str, embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        """Top-k por similaridade cosseno, com ``score`` já normalizado."""

    def get_chunks(
        self, tenant_id: str, ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Recupera trechos por id — usado para hits exclusivos do BM25."""

    def all_chunks(self, tenant_id: str) -> list[tuple[str, str]]:
        """(chunk_id, texto) usado para reconstruir o BM25 na subida."""

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        """Agregação por documento, para a interface administrativa."""

    def chunk_ids_for_document(
        self, tenant_id: str, document_id: str
    ) -> list[str]:
        """Ids de todos os chunks de um documento (para rollback/deleção)."""

    def tenant_ids(self) -> set[str]:
        """Todos os tenants presentes — apenas usado no warmup do BM25."""

    def upsert(
        self,
        tenant_id: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Insere ou substitui os trechos e seus embeddings."""

    def delete_ids(self, tenant_id: str, ids: list[str]) -> None:
        """Remove chunks específicos, tipicamente após reindexação parcial."""

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        """Remove todos os chunks de um documento."""

    def update_document_status(
        self,
        tenant_id: str,
        document_id: str,
        *,
        status: str,
        status_label: str,
        validated_by: str | None,
        validated_at: str | None,
    ) -> int:
        """Registra decisão humana sobre a vigência sem recalcular embeddings."""
