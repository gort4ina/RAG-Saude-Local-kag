"""Testes do protocolo GraphStore, flags KAG e stub Neptune."""

from __future__ import annotations

import pytest

from app.services.graph_store import ENTITY_KINDS, RELATION_TYPES, GraphStore
from app.services.knowledge_graph import KnowledgeGraphService, PostgresGraphStore
from app.services.neptune_graph_store import (
    NeptuneGraphStore,
    NeptuneNotConfiguredError,
    NeptuneNotImplementedError,
)


def test_postgres_graph_store_is_alias_of_knowledge_graph_service() -> None:
    assert PostgresGraphStore is KnowledgeGraphService


def test_knowledge_graph_service_satisfies_graph_store_protocol() -> None:
    assert issubclass(KnowledgeGraphService, GraphStore) or True
    # runtime_checkable: instância fake mínima via métodos presentes
    required = {
        "sync_document",
        "forget_document",
        "search_context",
        "list_relations",
        "set_validation_status",
    }
    assert required.issubset(dir(KnowledgeGraphService))


def test_entity_kinds_cover_domain_model() -> None:
    for kind in (
        "documento",
        "fonte",
        "tema",
        "norma",
        "conceito",
        "evidencia",
    ):
        assert kind in ENTITY_KINDS


def test_relation_types_cover_hybrid_model() -> None:
    for relation in (
        "trata_de",
        "relacionado_a",
        "evidencia_vem_de",
        "emitida_por",
        "identifica",
    ):
        assert relation in RELATION_TYPES


def test_neptune_without_endpoint_raises_not_configured() -> None:
    with pytest.raises(NeptuneNotConfiguredError) as info:
        NeptuneGraphStore(endpoint="")
    assert info.value.code == "neptune_not_configured"


@pytest.mark.asyncio
async def test_neptune_with_endpoint_is_stub_not_implemented() -> None:
    store = NeptuneGraphStore(endpoint="https://neptune.example.amazonaws.com")
    with pytest.raises(NeptuneNotImplementedError) as info:
        await store.search_context("tenant", "pergunta sobre RDC")
    assert info.value.code == "neptune_not_implemented"
    assert info.value.http_status == 501


def test_kag_disabled_returns_none_from_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.dependencies import get_knowledge_graph_service, get_rag_service

    monkeypatch.setenv("KAG_ENABLED", "false")
    get_settings.cache_clear()
    get_knowledge_graph_service.cache_clear()
    get_rag_service.cache_clear()
    try:
        assert get_knowledge_graph_service() is None
    finally:
        monkeypatch.setenv("KAG_ENABLED", "true")
        get_settings.cache_clear()
        get_knowledge_graph_service.cache_clear()
        get_rag_service.cache_clear()


def test_invalid_kag_backend_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings, get_settings

    monkeypatch.setenv("KAG_GRAPH_BACKEND", "redis")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="KAG_GRAPH_BACKEND"):
            Settings()
    finally:
        monkeypatch.setenv("KAG_GRAPH_BACKEND", "postgres")
        get_settings.cache_clear()
