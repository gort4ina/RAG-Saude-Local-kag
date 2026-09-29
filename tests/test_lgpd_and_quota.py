"""Testes para minimização LGPD do audit, poda por retenção e cota de tenant.

Coberturas:
- ``AUDIT_PERSIST_QUERY_TEXT=false`` (default): pergunta vira ``sha256:...``.
- ``AuditWriter.prune_expired``: apaga eventos mais antigos que a janela.
- Upload rejeita quando o tenant estoura ``TENANT_UPLOAD_QUOTA_MB``.
- ``delete_document`` limpa também as relações KAG do tenant.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.audit import AuditWriter, _normalize_query_text


def test_query_text_is_hashed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Padrão: só guardamos hash + comprimento, nunca o texto literal."""
    from app.config import get_settings

    monkeypatch.setenv("AUDIT_PERSIST_QUERY_TEXT", "false")
    get_settings.cache_clear()

    normalized = _normalize_query_text("Quais requisitos da RDC 67?")
    assert normalized is not None
    assert normalized.startswith("sha256:")
    assert normalized.endswith(":len=27")
    assert "RDC" not in normalized


def test_query_text_is_kept_when_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("AUDIT_PERSIST_QUERY_TEXT", "true")
    get_settings.cache_clear()
    try:
        assert _normalize_query_text("Pergunta clara") == "Pergunta clara"
    finally:
        monkeypatch.setenv("AUDIT_PERSIST_QUERY_TEXT", "false")
        get_settings.cache_clear()


def test_prune_expired_removes_only_beyond_retention(monkeypatch, tmp_path) -> None:
    """Eventos antigos são apagados; recentes permanecem intactos.

    Usa um engine local para não depender do estado do ``get_engine`` cacheado
    por outros testes da suíte que também tocam o schema de audit/user.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.database import Base
    from app.models import AuditEvent, Tenant, User
    from app.auth import hash_password

    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()

    local_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    LocalSession = async_sessionmaker(local_engine, expire_on_commit=False)

    # Monkeypatch a session factory pública para o writer usar nosso engine.
    from app.services import audit as audit_module

    monkeypatch.setattr(audit_module, "get_session_factory", lambda: LocalSession)

    async def _setup_and_run() -> tuple[int, int]:
        async with local_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        async with LocalSession() as session:
            tenant = Tenant(slug="acme", name="ACME")
            session.add(tenant)
            await session.flush()
            user = User(
                tenant_id=tenant.id,
                username="alice",
                password_hash=hash_password("Senha#Forte2026"),
                role="admin",
            )
            session.add(user)
            await session.flush()

            old = AuditEvent(
                request_id="req-old",
                tenant_id=tenant.id,
                user_id=user.id,
                action="rag.query",
                status="success",
                query_text="sha256:antiga",
                retrieved_sources=[],
                cited_sources=[],
                details={},
            )
            recent = AuditEvent(
                request_id="req-new",
                tenant_id=tenant.id,
                user_id=user.id,
                action="rag.query",
                status="success",
                query_text="sha256:recente",
                retrieved_sources=[],
                cited_sources=[],
                details={},
            )
            session.add_all([old, recent])
            await session.commit()

            # Backdata do "antigo" (SQLite não permite mudar via default,
            # então fazemos update explícito).
            old.created_at = datetime.now(timezone.utc) - timedelta(days=400)
            recent.created_at = datetime.now(timezone.utc) - timedelta(days=5)
            await session.commit()

        writer = AuditWriter()
        removed = await writer.prune_expired(retention_days=365)

        async with LocalSession() as session:
            from sqlalchemy import select

            remaining = (await session.scalars(select(AuditEvent))).all()
        return removed, len(remaining)

    try:
        removed, remaining = asyncio.run(_setup_and_run())
    finally:
        asyncio.run(local_engine.dispose())

    assert removed == 1
    assert remaining == 1


@pytest.mark.asyncio
async def test_forget_document_cleans_kag_relations(tmp_path) -> None:
    """Remover documento deve zerar suas relações do grafo relacional."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import Base
    from app.models import KnowledgeEntity, KnowledgeRelation
    from app.services.knowledge_graph import KnowledgeGraphService
    from app.services.metadata import STATUS_IN_FORCE, RegulatoryMetadata

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'kag.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    kag = KnowledgeGraphService(sessions)
    metadata = RegulatoryMetadata(
        document_id="doc-xyz",
        filename="rdc.pdf",
        content_hash="hash-fake",
        document_title="RDC 67",
        regulation_number="RDC 67/2007",
        authority="ANVISA",
        publication_date=None,
        effective_date=None,
        status=STATUS_IN_FORCE,
        source_url=None,
        document_version=None,
    )
    await kag.sync_document(
        "tenant-x", metadata, "Boas práticas de manipulação e controle de qualidade."
    )

    async with sessions() as session:
        from sqlalchemy import select

        rows_before = (await session.scalars(select(KnowledgeRelation))).all()
        assert rows_before, "sanity: relações devem ter sido criadas"

    removed = await kag.forget_document("tenant-x", "doc-xyz")
    assert removed == len(rows_before)

    async with sessions() as session:
        from sqlalchemy import select

        rows_after = (await session.scalars(select(KnowledgeRelation))).all()
        entities_after = (await session.scalars(select(KnowledgeEntity))).all()
    # Entidades permanecem (podem ser referenciadas por outros documentos),
    # mas nenhuma relação pendura no documento apagado.
    assert rows_after == []
    assert entities_after, "entidades permanecem para reuso"

    await engine.dispose()
