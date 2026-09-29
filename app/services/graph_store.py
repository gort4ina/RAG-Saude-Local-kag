"""Contrato do grafo de conhecimento (KAG), independente do backend.

Dois provedores previstos:

- ``postgres`` (default local): SQLAlchemy sobre ``knowledge_entities`` /
  ``knowledge_relations`` — já implementado em ``KnowledgeGraphService``.
- ``neptune`` (produção futura): openCypher via HTTP no AWS Neptune.
  O stub em ``neptune_graph_store.py`` NÃO exige AWS para rodar localmente;
  só falha se alguém ativar ``KAG_GRAPH_BACKEND=neptune`` sem configurar o
  endpoint.

O ``RagService`` depende apenas deste protocolo. Relações do grafo são
orientação para o LLM; a citação obrigatória continua vindo dos trechos
do store vetorial.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.services.metadata import RegulatoryMetadata


# Tipos de nó do domínio. Mantemos aliases legados (orgao, assunto, vigencia)
# para não quebrar dados já indexados; os nomes canônicos novos são os
# listados abaixo.
ENTITY_KINDS = frozenset(
    {
        "documento",
        "fonte",
        "tema",
        "norma",
        "conceito",
        "evidencia",
        # legados (compatibilidade com índices anteriores)
        "orgao",
        "assunto",
        "vigencia",
    }
)

# Relações canônicas suportadas pelo extrator atual.
RELATION_TYPES = frozenset(
    {
        "trata_de",           # documento/norma → tema
        "relacionado_a",      # conceito → conceito
        "evidencia_vem_de",   # evidência → documento
        "emitida_por",        # norma → fonte
        "possui_status",      # norma → vigencia
        "regula",             # norma → tema/assunto (legado)
        "identifica",         # documento → norma
    }
)


@runtime_checkable
class GraphStore(Protocol):
    """Superfície pública consumida pelo ``RagService`` e pelos endpoints KAG."""

    async def sync_document(
        self, tenant_id: str, metadata: RegulatoryMetadata, text: str
    ) -> int:
        """Extrai e persiste relações do documento. Retorna quantas arestas."""

    async def forget_document(self, tenant_id: str, document_id: str) -> int:
        """Remove arestas cuja proveniência é ``document_id``."""

    async def search_context(
        self, tenant_id: str, question: str, limit: int = 8
    ) -> list:
        """Busca arestas relevantes para a pergunta (lista de ``KagRelation``)."""

    async def list_relations(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list:
        """Enumera arestas do tenant para revisão humana."""

    async def set_validation_status(
        self,
        tenant_id: str,
        relation_id: str,
        *,
        status: str,
        validated_by: str,
    ):
        """Registra decisão humana. Retorna a aresta atualizada ou ``None``."""
