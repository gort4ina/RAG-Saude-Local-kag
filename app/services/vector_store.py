"""Persistencia e consulta de trechos no ChromaDB local.

Garantia importante: vetores de modelos de embeddings diferentes nunca sao
misturados. O nome do modelo fica gravado nos metadados da colecao; se a
configuracao mudar, a operacao e recusada com
``EmbeddingModelMismatchError`` e a colecao existente permanece intacta.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import chromadb

from app.errors import EmbeddingModelMismatchError, VectorStoreUnavailableError

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL_KEY = "embedding_model"
_STATUS_HEADER = re.compile(r"(?m)^Status informado:.*$")


class VectorStore:
    """Encapsula a colecao vetorial persistente."""

    def __init__(
        self,
        path: Path,
        collection_name: str,
        *,
        embedding_model: str = "",
    ) -> None:
        self.path = path
        self.collection_name = collection_name
        self.embedding_model = embedding_model
        try:
            self._client = chromadb.PersistentClient(path=str(path))
            self.collection = self._open_collection()
        except EmbeddingModelMismatchError:
            raise
        except Exception as exc:  # pragma: no cover - depende do ambiente
            logger.exception(
                "chroma_initialization_failed",
                extra={"path": str(path), "collection": collection_name},
            )
            raise VectorStoreUnavailableError(
                "Nao foi possivel inicializar o ChromaDB."
            ) from exc

    # ------------------------------------------------------------------
    # Abertura da colecao e verificacao do modelo de embeddings
    # ------------------------------------------------------------------

    def _open_collection(self):  # noqa: ANN202 - tipo interno do chromadb
        try:
            collection = self._client.get_collection(name=self.collection_name)
        except Exception:
            return self._client.create_collection(
                name=self.collection_name,
                metadata={
                    "hnsw:space": "cosine",
                    _EMBEDDING_MODEL_KEY: self.embedding_model,
                },
            )

        self._assert_embedding_model(collection)
        return collection

    def _assert_embedding_model(self, collection: Any) -> None:
        if not self.embedding_model:
            return
        stored = (collection.metadata or {}).get(_EMBEDDING_MODEL_KEY)

        if stored and stored != self.embedding_model:
            logger.error(
                "chroma_embedding_model_mismatch",
                extra={
                    "collection": self.collection_name,
                    "stored": stored,
                    "configured": self.embedding_model,
                },
            )
            raise EmbeddingModelMismatchError(
                self.collection_name, str(stored), self.embedding_model
            )

        if stored:
            return

        # Colecao sem marcacao. Se estiver vazia, marcamos agora. Se ja tiver
        # vetores, apenas avisamos: nao da para saber qual modelo os gerou e
        # apagar dados do usuario nao e uma opcao.
        try:
            count = int(collection.count())
        except Exception:  # pragma: no cover
            count = -1

        if count == 0:
            try:
                collection.modify(
                    metadata={
                        "hnsw:space": "cosine",
                        _EMBEDDING_MODEL_KEY: self.embedding_model,
                    }
                )
            except Exception:  # pragma: no cover - versoes antigas do chromadb
                logger.warning(
                    "chroma_metadata_stamp_failed",
                    extra={"collection": self.collection_name},
                )
        elif count > 0:
            logger.warning(
                "chroma_embedding_model_unknown",
                extra={"collection": self.collection_name, "chunks": count},
            )

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def health(self) -> bool:
        """Ping simples da colecao. Nao levanta excecoes."""
        try:
            self.collection.count()
            return True
        except Exception:  # pragma: no cover
            logger.exception("chroma_health_failed")
            return False

    def count_chunks(self, tenant_id: str) -> int:
        try:
            result = self.collection.get(
                where={"tenant_id": tenant_id}, include=[]
            )
            return len(result.get("ids") or [])
        except Exception:  # pragma: no cover
            logger.exception("chroma_count_failed")
            return 0

    def query(
        self, tenant_id: str, embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        """Recupera os trechos mais proximos e converte distancia em score.

        Confia no cliente para já ter descartado o caso "coleção vazia": o
        ``RagService`` faz um ``count_chunks`` antes de gerar o embedding para
        evitar o custo de embedar em vão. Aqui uma nova contagem seria um
        round trip redundante contra o Chroma.
        """
        try:
            result = self.collection.query(
                query_embeddings=[embedding],
                n_results=max(1, int(limit)),
                where={"tenant_id": tenant_id},
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            logger.exception("chroma_query_failed")
            raise VectorStoreUnavailableError(
                "ChromaDB falhou ao consultar a colecao."
            ) from exc

        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        items: list[dict[str, Any]] = []
        for chunk_id, document, metadata, distance in zip(
            ids, documents, metadatas, distances
        ):
            items.append(
                {
                    "id": str(chunk_id),
                    "text": document,
                    "metadata": dict(metadata or {}),
                    "score": max(0.0, min(1.0, 1.0 - float(distance))),
                }
            )
        return items

    def get_chunks(
        self, tenant_id: str, ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Busca chunks por id (usado pelos acertos exclusivos do BM25)."""
        if not ids:
            return {}
        try:
            result = self.collection.get(
                ids=ids,
                where={"tenant_id": tenant_id},
                include=["documents", "metadatas"],
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("chroma_get_chunks_failed")
            raise VectorStoreUnavailableError(
                "ChromaDB falhou ao recuperar trechos por id."
            ) from exc

        found: dict[str, dict[str, Any]] = {}
        for chunk_id, document, metadata in zip(
            result.get("ids") or [],
            result.get("documents") or [],
            result.get("metadatas") or [],
        ):
            found[str(chunk_id)] = {
                "id": str(chunk_id),
                "text": document,
                "metadata": dict(metadata or {}),
            }
        return found

    def all_chunks(self, tenant_id: str) -> list[tuple[str, str]]:
        """Todos os ``(id, texto)`` da colecao, para reconstruir o BM25."""
        try:
            result = self.collection.get(
                where={"tenant_id": tenant_id}, include=["documents"]
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("chroma_all_chunks_failed")
            raise VectorStoreUnavailableError(
                "ChromaDB falhou ao varrer a colecao."
            ) from exc
        return [
            (str(chunk_id), str(document or ""))
            for chunk_id, document in zip(
                result.get("ids") or [], result.get("documents") or []
            )
        ]

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        """Agrupa trechos por documento para exibicao na interface."""
        try:
            result = self.collection.get(
                where={"tenant_id": tenant_id}, include=["metadatas"]
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("chroma_list_documents_failed")
            raise VectorStoreUnavailableError(
                "ChromaDB falhou ao listar documentos."
            ) from exc

        grouped: dict[tuple[str, str], int] = defaultdict(int)
        details: dict[tuple[str, str], dict[str, Any]] = {}
        for metadata in result.get("metadatas") or []:
            metadata = metadata or {}
            key = (
                str(metadata.get("document_id", "")),
                str(metadata.get("filename", "documento")),
            )
            grouped[key] += 1
            details.setdefault(
                key,
                {
                    "authority": metadata.get("authority"),
                    "regulation_number": metadata.get("regulation_number"),
                    "status": metadata.get("status"),
                    "publication_date": metadata.get("publication_date"),
                },
            )

        return [
            {
                "document_id": key[0],
                "filename": key[1],
                "chunks": count,
                **details.get(key, {}),
            }
            for key, count in sorted(grouped.items(), key=lambda item: item[0][1].lower())
        ]

    def document_ids(self, tenant_id: str) -> set[str]:
        """Ids de documento presentes na colecao."""
        return {
            str(item["document_id"])
            for item in self.list_documents(tenant_id)
        }

    def tenant_ids(self) -> set[str]:
        """Tenants existentes, usado somente para reconstruir índices na subida."""
        try:
            result = self.collection.get(include=["metadatas"])
        except Exception as exc:  # pragma: no cover
            raise VectorStoreUnavailableError(
                "ChromaDB falhou ao listar tenants."
            ) from exc
        return {
            str(metadata.get("tenant_id"))
            for metadata in (result.get("metadatas") or [])
            if metadata and metadata.get("tenant_id")
        }

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    def upsert(
        self,
        tenant_id: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Insere ou atualiza trechos e metadados."""
        if not tenant_id or any(
            str(metadata.get("tenant_id", "")) != tenant_id
            for metadata in metadatas
        ):
            raise ValueError("tenant_id ausente ou divergente nos metadados")
        try:
            self.collection.upsert(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("chroma_upsert_failed", extra={"count": len(ids)})
            raise VectorStoreUnavailableError(
                "Falha ao persistir trechos no ChromaDB."
            ) from exc

    def delete_ids(self, tenant_id: str, ids: list[str]) -> None:
        """Remove trechos especificos por id."""
        if not ids:
            return
        try:
            self.collection.delete(ids=ids, where={"tenant_id": tenant_id})
        except Exception as exc:  # pragma: no cover
            logger.exception("chroma_delete_ids_failed", extra={"count": len(ids)})
            raise VectorStoreUnavailableError(
                "Falha ao remover trechos do ChromaDB."
            ) from exc

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        """Remove todos os trechos de um documento."""
        try:
            self.collection.delete(
                where={
                    "$and": [
                        {"tenant_id": tenant_id},
                        {"document_id": document_id},
                    ]
                }
            )
        except Exception as exc:  # pragma: no cover
            logger.exception(
                "chroma_delete_failed", extra={"document_id": document_id}
            )
            raise VectorStoreUnavailableError(
                "Falha ao remover documento antigo do ChromaDB."
            ) from exc

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
        """Atualiza a confirmacao humana em todos os trechos do documento.

        Os embeddings existentes sao preservados. O cabecalho textual tambem
        e atualizado para que o contexto entregue ao modelo nao contradiga os
        metadados usados pela auditoria de citacoes.
        """
        where = {
            "$and": [
                {"tenant_id": tenant_id},
                {"document_id": document_id},
            ]
        }
        try:
            result = self.collection.get(
                where=where,
                include=["documents", "metadatas", "embeddings"],
            )
            ids = [str(item) for item in (result.get("ids") or [])]
            if not ids:
                return 0

            documents = [
                _STATUS_HEADER.sub(f"Status informado: {status_label}", str(text or ""))
                for text in (result.get("documents") or [])
            ]
            metadatas: list[dict[str, Any]] = []
            for current in result.get("metadatas") or []:
                updated = dict(current or {})
                updated["status"] = status
                if validated_by and validated_at:
                    updated["validated_by"] = validated_by
                    updated["validated_at"] = validated_at
                    updated["validation_method"] = "confirmacao_manual_na_aplicacao"
                else:
                    updated.pop("validated_by", None)
                    updated.pop("validated_at", None)
                    updated.pop("validation_method", None)
                metadatas.append(updated)

            self.collection.update(
                ids=ids,
                documents=documents,
                embeddings=result.get("embeddings"),
                metadatas=metadatas,
            )
            return len(ids)
        except Exception as exc:  # pragma: no cover - depende do ChromaDB
            logger.exception(
                "chroma_status_update_failed",
                extra={"document_id": document_id, "status": status},
            )
            raise VectorStoreUnavailableError(
                "Falha ao atualizar a vigencia do documento no ChromaDB."
            ) from exc

    def chunk_ids_for_document(self, tenant_id: str, document_id: str) -> list[str]:
        """Ids dos trechos de um documento (para rollback de reindexacao)."""
        try:
            result = self.collection.get(
                where={
                    "$and": [
                        {"tenant_id": tenant_id},
                        {"document_id": document_id},
                    ]
                }
            )
        except Exception as exc:  # pragma: no cover
            logger.exception(
                "chroma_ids_for_document_failed", extra={"document_id": document_id}
            )
            raise VectorStoreUnavailableError(
                "ChromaDB falhou ao localizar trechos do documento."
            ) from exc
        return [str(chunk_id) for chunk_id in (result.get("ids") or [])]
