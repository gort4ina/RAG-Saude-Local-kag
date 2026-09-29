"""Camada KAG: relações explícitas, rastreáveis e isoladas por tenant.

Ela complementa a RAG textual. Relações derivadas automaticamente nunca são
tratadas como confirmadas; a LLM recebe esse material apenas como orientação
e continua obrigada a citar os trechos recuperados do documento original.

Duas mudanças de arquitetura relevantes vivem aqui:

1. **Extrator plugável.** ``RelationExtractor`` é a interface que separa "o
   que" é extraído de "como" é extraído. Hoje temos o
   ``DictionaryRelationExtractor``, determinístico e barato. Um
   ``LLMAssistedRelationExtractor`` cabe no mesmo contrato: basta emitir
   ``ExtractedRelation`` com ``extracted_by="llm"`` e ``confidence < 1.0``,
   que a promoção para "validada" continua exigindo humano.

2. **Extração por chunk estrutural.** ``sync_document`` percorre os
   ``StructuredChunk`` do documento (artigo por artigo, seção por seção) e
   guarda o rótulo do chunk em ``source_span``. Isso deixa cada aresta
   apontando para o pedaço do texto que a gerou, o que é o que torna o KAG
   auditável e revisável por um humano de compliance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import KnowledgeEntity, KnowledgeRelation
from app.services.metadata import RegulatoryMetadata, STATUS_IN_FORCE, STATUS_REVOKED
from app.services.text_splitter import StructuredChunk, split_structured


# ---------------------------------------------------------------------------
# Modelos de saída
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KagRelation:
    """Aresta pronta para prompt/UI/auditoria."""

    id: str
    relation: str
    subject: str
    subject_kind: str
    object: str
    object_kind: str
    source_reference: str | None
    source_document_id: str | None
    validation_status: str
    extracted_by: str = "dictionary"
    confidence: float = 1.0
    source_span: str | None = None
    validated_by: str | None = None
    validated_at: datetime | None = None

    def as_prompt_line(self) -> str:
        return (
            f"{self.subject} → {self.relation} → {self.object} "
            f"(fonte: {self.source_reference or 'n/d'}; "
            f"status: {self.validation_status})"
        )


@dataclass(frozen=True, slots=True)
class ExtractedRelation:
    """Aresta candidata devolvida por um extractor, ainda sem persistência.

    ``source_span`` é o rótulo estrutural (ex.: "Art. 5º" ou "Anexo II") do
    trecho de onde a evidência veio. Serve para auditoria: quem revisar a
    relação sabe onde ler no documento original.

    ``source_kind`` / ``source_name`` defaultam para a norma do documento
    (comportamento legado). O extrator pode apontar para ``documento``,
    ``conceito`` ou ``evidencia`` quando a aresta não parte da norma.
    """

    relation: str
    target_kind: str
    target_name: str
    confidence: float = 1.0
    source_span: str | None = None
    extracted_by: str = "dictionary"
    source_kind: str | None = None
    source_name: str | None = None


# ---------------------------------------------------------------------------
# Extratores
# ---------------------------------------------------------------------------


class RelationExtractor(Protocol):
    """Contrato mínimo de um extractor."""

    name: str

    def extract(
        self,
        *,
        metadata: RegulatoryMetadata,
        chunks: list[StructuredChunk],
    ) -> Iterable[ExtractedRelation]:
        ...


# Dicionário de tópicos → aliases (variações grafadas com/sem acento e sinônimos).
# A lista propositalmente conservadora: uma extração fraca é preferível a
# uma extração ruidosa que polua o KAG.
TOPICS: dict[str, tuple[str, ...]] = {
    "controle de qualidade": (
        "controle de qualidade",
        "qualidade",
        "peso médio",
        "peso medio",
    ),
    "boas práticas de manipulação": (
        "boas práticas",
        "boas praticas",
        "manipulação",
        "manipulacao",
    ),
    "procedimento operacional padrão": (
        "pop",
        "procedimento operacional",
    ),
    "água purificada": (
        "água purificada",
        "agua purificada",
        "osmose",
    ),
}


@dataclass(slots=True)
class DictionaryRelationExtractor:
    """Extrator determinístico baseado em dicionário de tópicos.

    Emite (kinds canônicos + aliases legados para compatibilidade):

    - ``documento --identifica--> norma``;
    - ``norma --emitida_por--> fonte`` (e alias legado ``orgao``);
    - ``norma --possui_status--> vigencia``;
    - ``documento --trata_de--> tema`` e ``norma --regula--> assunto``;
    - ``evidencia --evidencia_vem_de--> documento`` por chunk com hit;
    - ``conceito --relacionado_a--> conceito`` quando dois tópicos coocorrem
      no mesmo chunk (relação fraca, sempre ``pendente_de_validacao``).

    Repetir por chunk permite anotar ``source_span``. O ``UniqueConstraint``
    deduplica no banco; o ``source_span`` do primeiro hit é preservado.
    """

    name: str = "dictionary"
    topics: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(TOPICS))

    def extract(
        self,
        *,
        metadata: RegulatoryMetadata,
        chunks: list[StructuredChunk],
    ) -> Iterable[ExtractedRelation]:
        document_label = metadata.filename
        norma_label = (
            metadata.regulation_number or metadata.document_title or metadata.filename
        )

        # Documento ↔ norma (cabeçalho).
        yield ExtractedRelation(
            relation="identifica",
            target_kind="norma",
            target_name=norma_label,
            source_span="cabeçalho",
            extracted_by=self.name,
            source_kind="documento",
            source_name=document_label,
        )

        # Fonte emissora (canônico) + alias legado ``orgao``.
        if metadata.authority:
            yield ExtractedRelation(
                relation="emitida_por",
                target_kind="fonte",
                target_name=metadata.authority,
                source_span="cabeçalho",
                extracted_by=self.name,
            )
            yield ExtractedRelation(
                relation="emitida_por",
                target_kind="orgao",
                target_name=metadata.authority,
                source_span="cabeçalho",
                extracted_by=self.name,
            )

        yield ExtractedRelation(
            relation="possui_status",
            target_kind="vigencia",
            target_name=metadata.status,
            source_span="cabeçalho",
            extracted_by=self.name,
        )

        for chunk in chunks:
            body_lower = chunk.body.casefold()
            span = chunk.context.item_label() or chunk.context.section or (
                chunk.context.chapter or chunk.context.title or "trecho"
            )
            matched_topics: list[str] = []
            for topic, aliases in self.topics.items():
                if not any(alias in body_lower for alias in aliases):
                    continue
                matched_topics.append(topic)
                # Canônico: documento trata_de tema.
                yield ExtractedRelation(
                    relation="trata_de",
                    target_kind="tema",
                    target_name=topic,
                    source_span=span,
                    extracted_by=self.name,
                    source_kind="documento",
                    source_name=document_label,
                )
                # Legado: norma regula assunto (UI/testes já conhecem).
                yield ExtractedRelation(
                    relation="regula",
                    target_kind="assunto",
                    target_name=topic,
                    source_span=span,
                    extracted_by=self.name,
                )
                # Evidência rastreável → documento.
                evidence_label = f"{document_label}::{span}"
                yield ExtractedRelation(
                    relation="evidencia_vem_de",
                    target_kind="documento",
                    target_name=document_label,
                    source_span=span,
                    extracted_by=self.name,
                    source_kind="evidencia",
                    source_name=evidence_label,
                )

            # Coocorrência no mesmo chunk → conceito relacionado_a conceito.
            for index, left in enumerate(matched_topics):
                for right in matched_topics[index + 1 :]:
                    yield ExtractedRelation(
                        relation="relacionado_a",
                        target_kind="conceito",
                        target_name=right,
                        source_span=span,
                        extracted_by=self.name,
                        source_kind="conceito",
                        source_name=left,
                        confidence=0.6,
                    )


# ---------------------------------------------------------------------------
# Serviço
# ---------------------------------------------------------------------------


class KnowledgeGraphService:
    """Persistência e consulta de arestas KAG, isoladas por tenant."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        extractor: RelationExtractor | None = None,
    ) -> None:
        self.sessions = sessions
        self.extractor: RelationExtractor = (
            extractor if extractor is not None else DictionaryRelationExtractor()
        )

    async def _entity(
        self, session: AsyncSession, tenant_id: str, kind: str, name: str
    ) -> KnowledgeEntity:
        item = await session.scalar(
            select(KnowledgeEntity).where(
                KnowledgeEntity.tenant_id == tenant_id,
                KnowledgeEntity.kind == kind,
                KnowledgeEntity.name == name,
            )
        )
        if item is None:
            item = KnowledgeEntity(tenant_id=tenant_id, kind=kind, name=name)
            session.add(item)
            await session.flush()
        return item

    async def sync_document(
        self, tenant_id: str, metadata: RegulatoryMetadata, text: str
    ) -> int:
        """Regrava todas as relações de ``document_id`` extraídas por chunk.

        Retorna quantas relações foram persistidas para instrumentação.
        """
        document_name = (
            metadata.regulation_number or metadata.document_title or metadata.filename
        )
        # Confiança do dicionário é alta, mas a validação é "pendente" a menos
        # que a vigência tenha sido confirmada por humano. Relações entre
        # conceitos (confidence < 1) ficam sempre pendentes.
        status_hint = (
            "validada"
            if metadata.status in {STATUS_IN_FORCE, STATUS_REVOKED}
            else "pendente_de_validacao"
        )

        structural_chunks = split_structured(text)

        persisted = 0
        seen: set[tuple[str, str, str, str, str]] = set()
        async with self.sessions() as session:
            await session.execute(
                KnowledgeRelation.__table__.delete().where(
                    KnowledgeRelation.tenant_id == tenant_id,
                    KnowledgeRelation.source_document_id == metadata.document_id,
                )
            )
            default_source = await self._entity(
                session, tenant_id, "norma", document_name
            )

            for extracted in self.extractor.extract(
                metadata=metadata, chunks=structural_chunks
            ):
                source_kind = extracted.source_kind or "norma"
                source_name = extracted.source_name or document_name
                key = (
                    source_kind,
                    source_name,
                    extracted.relation,
                    extracted.target_kind,
                    extracted.target_name,
                )
                if key in seen:
                    continue
                seen.add(key)

                if source_kind == "norma" and source_name == document_name:
                    source = default_source
                else:
                    source = await self._entity(
                        session, tenant_id, source_kind, source_name
                    )
                target = await self._entity(
                    session, tenant_id, extracted.target_kind, extracted.target_name
                )
                edge_status = (
                    "pendente_de_validacao"
                    if extracted.confidence < 1.0
                    else status_hint
                )
                session.add(
                    KnowledgeRelation(
                        tenant_id=tenant_id,
                        source_entity_id=source.id,
                        relation=extracted.relation,
                        target_entity_id=target.id,
                        source_document_id=metadata.document_id,
                        source_reference=metadata.filename,
                        source_span=extracted.source_span,
                        validation_status=edge_status,
                        extracted_by=extracted.extracted_by,
                        confidence=extracted.confidence,
                    )
                )
                persisted += 1
            await session.commit()
        return persisted

    async def forget_document(self, tenant_id: str, document_id: str) -> int:
        """Apaga toda relação cuja proveniência é ``document_id``.

        Chamado antes do delete no vetor, para o grafo nunca pendurar arestas
        órfãs.
        """
        async with self.sessions() as session:
            result = await session.execute(
                KnowledgeRelation.__table__.delete().where(
                    KnowledgeRelation.tenant_id == tenant_id,
                    KnowledgeRelation.source_document_id == document_id,
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def search_context(
        self, tenant_id: str, question: str, limit: int = 8
    ) -> list[KagRelation]:
        terms = [term for term in question.casefold().split() if len(term) >= 4]
        if not terms:
            return []
        async with self.sessions() as session:
            entity = KnowledgeEntity
            target = KnowledgeEntity.__table__.alias("target_entity")
            query = (
                select(
                    KnowledgeRelation,
                    entity.name,
                    entity.kind,
                    target.c.name.label("target_name"),
                    target.c.kind.label("target_kind"),
                )
                .join(entity, KnowledgeRelation.source_entity_id == entity.id)
                .join(target, KnowledgeRelation.target_entity_id == target.c.id)
                .where(
                    KnowledgeRelation.tenant_id == tenant_id,
                    KnowledgeRelation.validation_status != "rejeitada",
                )
            )
            conditions = [
                entity.name.ilike(f"%{term}%") | target.c.name.ilike(f"%{term}%")
                for term in terms
            ]
            rows = (
                await session.execute(query.where(or_(*conditions)).limit(limit))
            ).all()
        return [self._to_kag_relation(row) for row in rows]

    async def list_relations(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[KagRelation]:
        """Enumera relações para revisão humana no painel administrativo."""
        async with self.sessions() as session:
            entity = KnowledgeEntity
            target = KnowledgeEntity.__table__.alias("target_entity")
            query = (
                select(
                    KnowledgeRelation,
                    entity.name,
                    entity.kind,
                    target.c.name.label("target_name"),
                    target.c.kind.label("target_kind"),
                )
                .join(entity, KnowledgeRelation.source_entity_id == entity.id)
                .join(target, KnowledgeRelation.target_entity_id == target.c.id)
                .where(KnowledgeRelation.tenant_id == tenant_id)
                .order_by(KnowledgeRelation.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            )
            if status:
                query = query.where(KnowledgeRelation.validation_status == status)
            rows = (await session.execute(query)).all()
        return [self._to_kag_relation(row) for row in rows]

    async def set_validation_status(
        self,
        tenant_id: str,
        relation_id: str,
        *,
        status: str,
        validated_by: str,
    ) -> KagRelation | None:
        """Registra decisão humana. Retorna ``None`` se a aresta não existir.

        ``status`` esperados: ``"validada"``, ``"rejeitada"``,
        ``"pendente_de_validacao"``. Rejeitar mantém a aresta no banco para
        auditoria; se quiser removê-la definitivamente, use um endpoint
        separado (não implementado — decisão intencional para preservar
        histórico de revisão).
        """
        async with self.sessions() as session:
            relation = await session.scalar(
                select(KnowledgeRelation).where(
                    KnowledgeRelation.tenant_id == tenant_id,
                    KnowledgeRelation.id == relation_id,
                )
            )
            if relation is None:
                return None
            relation.validation_status = status
            relation.validated_by = validated_by
            relation.validated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(relation)

            source = await session.get(KnowledgeEntity, relation.source_entity_id)
            target = await session.get(KnowledgeEntity, relation.target_entity_id)
            return self._to_kag_relation(
                (relation, source.name, source.kind, target.name, target.kind)
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_kag_relation(row) -> KagRelation:
        relation, source_name, source_kind, target_name, target_kind = row
        return KagRelation(
            id=str(relation.id),
            relation=relation.relation,
            subject=source_name,
            subject_kind=source_kind,
            object=target_name,
            object_kind=target_kind,
            source_reference=relation.source_reference,
            source_document_id=relation.source_document_id,
            validation_status=relation.validation_status,
            extracted_by=getattr(relation, "extracted_by", "dictionary"),
            confidence=float(getattr(relation, "confidence", 1.0) or 1.0),
            source_span=getattr(relation, "source_span", None),
            validated_by=getattr(relation, "validated_by", None),
            validated_at=getattr(relation, "validated_at", None),
        )


# Alias explícito: este serviço É o GraphStore local (PostgreSQL).
PostgresGraphStore = KnowledgeGraphService
