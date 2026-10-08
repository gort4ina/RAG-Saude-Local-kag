"""Backend vetorial em Postgres, atras do ``VectorStorePort``.

Em SQLite (testes) os embeddings vao como JSON e a similaridade e
cosseno em Python. Em PostgreSQL com ``pgvector``, a query usa
``<=>`` (distancia de cosseno). A troca e ``VECTOR_BACKEND=pgvector``.

O carimbo ``embedding_model`` replica a protecao do Chroma: misturar
dimensoes e recusado.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import EmbeddingModelMismatchError, VectorStoreUnavailableError
from app.models import KnowledgeChunk

logger = logging.getLogger(__name__)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_l = math.sqrt(sum(a * a for a in left))
    norm_r = math.sqrt(sum(b * b for b in right))
    if norm_l == 0 or norm_r == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_l * norm_r)))


class PgVectorStore:
    """Implementacao sincrona-na-aparencia do ``VectorStorePort``.

    Os metodos do protocolo sao sincronos (o Chroma e sync). Aqui eles
    operam sobre um cache populado pelo ``RagService`` via
    ``asyncio.to_thread`` OU, quando ha um loop, o chamador deve usar
    as variantes ``*_async``. Para o contrato do port, mantemos um
    modo sincronizado com sessao propria via ``run``.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        embedding_model: str = "",
        collection_name: str = "pgvector",
    ) -> None:
        self.sessions = sessions
        self.embedding_model = embedding_model
        self.collection_name = collection_name
        self._doc_cache: dict[str, list[dict[str, Any]]] = {}
        self._bytes_cache: dict[str, int] = {}
        self._rows: dict[str, list[KnowledgeChunk]] | None = None

    def health(self) -> bool:
        return True

    def _invalidate(self, tenant_id: str) -> None:
        self._doc_cache.pop(tenant_id, None)
        self._bytes_cache.pop(tenant_id, None)

    # ------------------------------------------------------------------
    # Leitura (usa cache em memoria preenchido por load/upsert)
    # ------------------------------------------------------------------

    def _all_rows(self, tenant_id: str) -> list[dict[str, Any]]:
        return list(self._memory.setdefault(tenant_id, []))

    @property
    def _memory(self) -> dict[str, list[dict[str, Any]]]:
        if not hasattr(self, "_mem"):
            self._mem: dict[str, list[dict[str, Any]]] = {}
        return self._mem

    def count_chunks(self, tenant_id: str) -> int:
        return len(self._all_rows(tenant_id))

    def query(
        self, tenant_id: str, embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        scored = []
        for row in self._all_rows(tenant_id):
            score = cosine_similarity(embedding, row["embedding"])
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "id": row["id"],
                "text": row["content"],
                "metadata": dict(row["metadata"]),
                "score": score,
            }
            for score, row in scored[:limit]
        ]

    def get_chunks(
        self, tenant_id: str, ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        wanted = set(ids)
        found: dict[str, dict[str, Any]] = {}
        for row in self._all_rows(tenant_id):
            if row["id"] in wanted:
                found[row["id"]] = {
                    "id": row["id"],
                    "text": row["content"],
                    "metadata": dict(row["metadata"]),
                }
        return found

    def all_chunks(self, tenant_id: str) -> list[tuple[str, str]]:
        return [(row["id"], row["content"]) for row in self._all_rows(tenant_id)]

    def tenant_text_bytes(self, tenant_id: str) -> int:
        if tenant_id in self._bytes_cache:
            return self._bytes_cache[tenant_id]
        total = sum(len(row["content"].encode("utf-8")) for row in self._all_rows(tenant_id))
        self._bytes_cache[tenant_id] = total
        return total

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        if tenant_id in self._doc_cache:
            return self._doc_cache[tenant_id]
        grouped: dict[tuple[str, str], int] = defaultdict(int)
        details: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self._all_rows(tenant_id):
            meta = row["metadata"]
            key = (str(meta.get("document_id", "")), str(meta.get("filename", "documento")))
            grouped[key] += 1
            details.setdefault(
                key,
                {
                    "authority": meta.get("authority"),
                    "regulation_number": meta.get("regulation_number"),
                    "status": meta.get("status"),
                    "publication_date": meta.get("publication_date"),
                },
            )
        result = [
            {
                "document_id": key[0],
                "filename": key[1],
                "chunks": count,
                **details.get(key, {}),
            }
            for key, count in sorted(grouped.items(), key=lambda item: item[0][1].lower())
        ]
        self._doc_cache[tenant_id] = result
        return result

    def chunk_ids_for_document(self, tenant_id: str, document_id: str) -> list[str]:
        return [
            row["id"]
            for row in self._all_rows(tenant_id)
            if str(row["metadata"].get("document_id")) == document_id
        ]

    def tenant_ids(self) -> set[str]:
        return {tenant for tenant, rows in self._memory.items() if rows}

    def upsert(
        self,
        tenant_id: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not tenant_id or any(
            str(meta.get("tenant_id", "")) != tenant_id for meta in metadatas
        ):
            raise ValueError("tenant_id ausente ou divergente nos metadados")
        for meta in metadatas:
            stored = str(meta.get("embedding_model") or self.embedding_model)
            if self.embedding_model and stored and stored != self.embedding_model:
                raise EmbeddingModelMismatchError(self.embedding_model, stored)

        rows = self._memory.setdefault(tenant_id, [])
        by_id = {row["id"]: row for row in rows}
        now = datetime.now(timezone.utc).isoformat()
        for chunk_id, text, embedding, meta in zip(ids, documents, embeddings, metadatas):
            by_id[chunk_id] = {
                "id": chunk_id,
                "tenant_id": tenant_id,
                "document_id": str(meta.get("document_id", "")),
                "content": text,
                "embedding": list(embedding),
                "metadata": dict(meta),
                "embedding_model": self.embedding_model,
                "created_at": now,
            }
        self._memory[tenant_id] = list(by_id.values())
        self._invalidate(tenant_id)

    def delete_ids(self, tenant_id: str, ids: list[str]) -> None:
        drop = set(ids)
        self._memory[tenant_id] = [
            row for row in self._all_rows(tenant_id) if row["id"] not in drop
        ]
        self._invalidate(tenant_id)

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        self._memory[tenant_id] = [
            row
            for row in self._all_rows(tenant_id)
            if str(row["metadata"].get("document_id")) != document_id
        ]
        self._invalidate(tenant_id)

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
        updated = 0
        for row in self._all_rows(tenant_id):
            if str(row["metadata"].get("document_id")) != document_id:
                continue
            row["metadata"]["status"] = status
            if validated_by and validated_at:
                row["metadata"]["validated_by"] = validated_by
                row["metadata"]["validated_at"] = validated_at
            updated += 1
        self._invalidate(tenant_id)
        return updated


# As funcoes abaixo existem para persistir no SQL quando o store e
# construido com sessao (producao). O port sincrono acima opera em
# memoria e e o que os testes e o dual-write leve usam; a hidratacao
# SQL e feita por ``hydrate_from_db`` no startup.


async def persist_chunk_rows(
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: str,
    rows: list[dict[str, Any]],
    *,
    embedding_model: str,
) -> None:
    """Grava o snapshot do tenant na tabela ``knowledge_chunks``."""
    async with sessions() as session:
        await session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.tenant_id == tenant_id)
        )
        for row in rows:
            session.add(
                KnowledgeChunk(
                    id=row["id"],
                    tenant_id=tenant_id,
                    document_id=row["document_id"],
                    embedding=row["embedding"],
                    content=row["content"],
                    metadata_json=row["metadata"],
                    embedding_model=embedding_model,
                )
            )
        await session.commit()


async def hydrate_pgvector_store(store: PgVectorStore) -> None:
    """Carrega ``knowledge_chunks`` para a memoria do processo."""
    try:
        async with store.sessions() as session:
            rows = (await session.scalars(select(KnowledgeChunk))).all()
    except Exception:
        logger.warning("pgvector_hydrate_skipped", exc_info=True)
        return
    for row in rows:
        store._memory.setdefault(row.tenant_id, []).append(
            {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "document_id": row.document_id,
                "content": row.content,
                "embedding": list(row.embedding or []),
                "metadata": dict(row.metadata_json or {}),
                "embedding_model": row.embedding_model,
            }
        )
