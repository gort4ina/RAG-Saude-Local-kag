"""Contrato de embeddings, independente do provedor.

O ``RagService`` deixa de falar com ``OllamaClient.embed`` direto. Trocar
para SentenceTransformer, bge-m3 ou outra API vira uma implementacao
deste protocolo — sem reabrir o orquestrador.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Gera vetores para uma lista de textos."""

    @property
    def model(self) -> str:
        """Nome do modelo — carimbado no vetor store para evitar mistura."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Devolve um vetor por texto, na mesma ordem."""


class OllamaEmbeddingProvider:
    """Adaptador fino sobre o cliente Ollama ja existente."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def model(self) -> str:
        return str(getattr(self._client, "embedding_model", "") or "")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._client.embed(texts)
