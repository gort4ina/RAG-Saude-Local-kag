"""Testes da camada KAG estendida: extractor por chunk + validação humana."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.services.knowledge_graph import (
    DictionaryRelationExtractor,
    KnowledgeGraphService,
)
from app.services.metadata import STATUS_IN_FORCE, RegulatoryMetadata


def _make_kag(tmp_path: Path) -> tuple[KnowledgeGraphService, callable, callable]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'kag.db').as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _teardown() -> None:
        await engine.dispose()

    return KnowledgeGraphService(sessions), _setup, _teardown


@pytest.mark.asyncio
async def test_dictionary_extractor_emits_relation_per_chunk_with_source_span(
    tmp_path: Path,
) -> None:
    """Cada chunk onde um tópico aparece gera aresta, com ``source_span``."""
    kag, setup, teardown = _make_kag(tmp_path)
    await setup()

    metadata = RegulatoryMetadata(
        document_id="doc-1",
        filename="rdc.md",
        content_hash="h",
        document_title="RDC 67",
        regulation_number="RDC 67/2007",
        authority="ANVISA",
        status=STATUS_IN_FORCE,
    )
    text = (
        "# RDC 67/2007\n\n"
        "Art. 1º Estabelece boas praticas de manipulacao.\n\n"
        "Art. 5º A farmacia deve manter controle de qualidade dos insumos.\n\n"
        "Art. 12 Procedimento operacional padrão POP para agua purificada."
    )
    try:
        persisted = await kag.sync_document("tenant-x", metadata, text)
        assert persisted >= 4  # emitida_por + status + regula(*)

        relations = await kag.list_relations("tenant-x")
        by_relation = {r.relation for r in relations}
        assert {"emitida_por", "possui_status", "regula", "trata_de", "identifica"}.issubset(
            by_relation
        )
        by_kind = {r.subject_kind for r in relations} | {
            r.object_kind for r in relations
        }
        assert "documento" in by_kind or "norma" in by_kind
        assert "tema" in by_kind or "assunto" in by_kind

        regulates = [r for r in relations if r.relation == "regula"]
        # Cada aresta 'regula' aponta para um trecho concreto do documento.
        spans = {r.source_span for r in regulates}
        assert any("Art. 1" in (s or "") for s in spans)
        assert all(r.extracted_by == "dictionary" for r in regulates)
    finally:
        await teardown()


@pytest.mark.asyncio
async def test_set_validation_status_records_human_decision(tmp_path: Path) -> None:
    """``validated_by`` e ``validated_at`` são preenchidos, aresta permanece."""
    kag, setup, teardown = _make_kag(tmp_path)
    await setup()

    metadata = RegulatoryMetadata(
        document_id="doc-1",
        filename="rdc.md",
        content_hash="h",
        authority="ANVISA",
        status=STATUS_IN_FORCE,
    )
    await kag.sync_document(
        "tenant-x", metadata, "Art. 1º Sobre controle de qualidade."
    )
    relations = await kag.list_relations("tenant-x")
    assert relations
    target = next(r for r in relations if r.relation == "regula")

    try:
        updated = await kag.set_validation_status(
            "tenant-x", target.id, status="validada", validated_by="alice"
        )
        assert updated is not None
        assert updated.validation_status == "validada"
        assert updated.validated_by == "alice"
        assert updated.validated_at is not None

        rejected = await kag.set_validation_status(
            "tenant-x", target.id, status="rejeitada", validated_by="alice"
        )
        assert rejected is not None
        # Rejeitar não apaga — mantemos histórico auditável.
        remaining = await kag.list_relations("tenant-x")
        assert any(r.id == target.id and r.validation_status == "rejeitada" for r in remaining)
    finally:
        await teardown()


@pytest.mark.asyncio
async def test_custom_extractor_is_plugged_via_constructor(tmp_path: Path) -> None:
    """A interface aceita substituir o extractor por outro (p.ex. LLM)."""
    from app.services.knowledge_graph import ExtractedRelation

    class FakeLLMExtractor:
        name = "llm"

        def extract(self, *, metadata, chunks):
            yield ExtractedRelation(
                relation="regula",
                target_kind="assunto",
                target_name="rastreabilidade",
                confidence=0.65,
                source_span="Art. 5",
                extracted_by=self.name,
            )

    kag, setup, teardown = _make_kag(tmp_path)
    await setup()
    kag.extractor = FakeLLMExtractor()

    metadata = RegulatoryMetadata(
        document_id="doc-1", filename="rdc.md", content_hash="h",
        authority="ANVISA", status=STATUS_IN_FORCE,
    )
    try:
        await kag.sync_document("tenant-x", metadata, "Art. 5º rastreabilidade.")
        relations = await kag.list_relations("tenant-x")
        llm_relations = [r for r in relations if r.extracted_by == "llm"]
        assert llm_relations
        assert llm_relations[0].confidence == pytest.approx(0.65)
    finally:
        await teardown()


@pytest.mark.asyncio
async def test_search_context_matches_accented_and_aliased_terms(
    tmp_path: Path,
) -> None:
    kag, setup, teardown = _make_kag(tmp_path)
    await setup()
    metadata = RegulatoryMetadata(
        document_id="doc-1",
        filename="rdc.md",
        content_hash="h",
        authority="ANVISA",
        regulation_number="RDC 67/2007",
        status=STATUS_IN_FORCE,
    )
    try:
        await kag.sync_document(
            "tenant-x", metadata, "Art. 1º Estabelece boas praticas de manipulacao."
        )
        hits = await kag.search_context(
            "tenant-x", "o que a Vigilância Sanitária e a ANVISA regulam?"
        )
        assert hits
        await kag.sync_document(
            "tenant-x",
            RegulatoryMetadata(
                document_id="doc-2",
                filename="outro.md",
                content_hash="h2",
                authority="Anvisa",
                status=STATUS_IN_FORCE,
            ),
            "Art. 2 texto.",
        )
        relations = await kag.list_relations("tenant-x")
        fontes = [r for r in relations if r.object_kind in {"fonte", "orgao"}]
        names = {r.object.casefold() for r in fontes}
        assert "anvisa" in names
    finally:
        await teardown()
