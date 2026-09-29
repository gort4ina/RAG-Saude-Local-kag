"""Orquestracao de ingestao, recuperacao hibrida e resposta fundamentada."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.errors import (
    EmptyDocumentError,
    IngestionInProgressError,
    RagError,
)
from app.logging_config import get_request_id
from app.schemas import (
    ChatResponse,
    DocumentSummary,
    KagRelationView,
    Source,
    UploadResponse,
)
from app.services.bm25_index import Bm25Index
from app.services.chunking import build_chunks
from app.services.citations import CitationAudit, audit_answer
from app.services.document_loader import document_hash, extract_document
from app.services.metadata import (
    STATUS_IN_FORCE,
    STATUS_UNVERIFIED,
    VALID_STATUSES,
    extract_metadata,
    load_sidecar,
    sidecar_path,
    status_label,
)
from app.services import metrics as prom_metrics
from app.services.ollama_client import OllamaClient, OllamaMetrics
from app.services.retrieval import Candidate, fuse, select_context
from app.services.safety import (
    DISCLAIMER,
    EMPTY_BASE_ANSWER,
    NO_EVIDENCE_ANSWER,
    build_system_prompt,
    build_user_prompt,
)
from app.services.vector_port import VectorStorePort
from app.services.graph_store import GraphStore
from app.services.knowledge_graph import KagRelation

logger = logging.getLogger(__name__)
LOCAL_TENANT_ID = "local"


@dataclass(frozen=True, slots=True)
class RagOptions:
    """Parametros de recuperacao e chunking.

    ``chunk_chars`` e ``chunk_overlap_chars`` sao CARACTERES, nao tokens.
    """

    retrieval_candidates: int = 12
    max_context_chunks: int = 4
    min_relevance_score: float = 0.45
    chunk_chars: int = 1_900
    chunk_overlap_chars: int = 285
    embed_batch_size: int = 16
    hybrid_search_enabled: bool = True
    rrf_k: int = 60
    dedup_similarity: float = 0.90
    min_chars_per_page: int = 40
    min_extraction_ratio: float = 0.20


@dataclass(slots=True)
class RetrievalOutcome:
    """Tudo o que a etapa de recuperacao produziu, incluindo metricas."""

    collection_count: int = 0
    selected: list[Candidate] = field(default_factory=list)
    fused_count: int = 0
    sources: list[Source] = field(default_factory=list)
    context_blocks: list[str] = field(default_factory=list)
    embed_ms: int = 0
    vector_query_ms: int = 0
    bm25_query_ms: int = 0
    fusion_ms: int = 0
    scores: list[float] = field(default_factory=list)
    early_exit_answer: str | None = None
    knowledge_relations: list[KagRelation] = field(default_factory=list)
    graph_query_ms: int = 0


class RagService:
    """Caso de uso principal da aplicacao."""

    def __init__(
        self,
        ollama: OllamaClient,
        store: VectorStorePort,
        options: RagOptions,
        *,
        bm25: Bm25Index | None = None,
        upload_path: Path | None = None,
        knowledge_graph: GraphStore | None = None,
    ) -> None:
        self.ollama = ollama
        self.store = store
        self.options = options
        self.bm25 = bm25 if bm25 is not None else Bm25Index()
        self._bm25_by_tenant: dict[str, Bm25Index] = {LOCAL_TENANT_ID: self.bm25}
        self.upload_path = upload_path
        self.knowledge_graph = knowledge_graph
        self._ingesting: set[str] = set()
        self._ingest_guard = asyncio.Lock()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def warmup(self) -> None:
        """Reconstroi um índice BM25 independente para cada tenant."""
        if not self.options.hybrid_search_enabled:
            return
        try:
            tenant_ids = await asyncio.to_thread(self.store.tenant_ids)
        except RagError as exc:
            logger.warning("bm25_warmup_skipped", extra={"code": exc.code})
            return
        for tenant_id in tenant_ids:
            chunks = await asyncio.to_thread(self.store.all_chunks, tenant_id)
            await asyncio.to_thread(self._bm25(tenant_id).rebuild, chunks)

    def _bm25(self, tenant_id: str) -> Bm25Index:
        return self._bm25_by_tenant.setdefault(tenant_id, Bm25Index())

    def bm25_size(self, tenant_id: str) -> int:
        return self._bm25(tenant_id).size

    async def _refresh_bm25(self, tenant_id: str) -> None:
        if not self.options.hybrid_search_enabled:
            return
        chunks = await asyncio.to_thread(self.store.all_chunks, tenant_id)
        await asyncio.to_thread(self._bm25(tenant_id).rebuild, chunks)

    # ------------------------------------------------------------------
    # Ingestao
    # ------------------------------------------------------------------

    async def ingest(
        self,
        path: Path,
        original_filename: str,
        content: bytes,
        *,
        tenant_id: str = LOCAL_TENANT_ID,
    ) -> UploadResponse:
        """Extrai, divide, vetoriza e persiste um documento.

        Se qualquer etapa falhar, a versao ja indexada do documento e
        preservada: os trechos antigos so sao removidos depois que os novos
        forem gravados com sucesso.
        """
        document_id = document_hash(content)
        ingest_key = f"{tenant_id}:{document_id}"

        async with self._ingest_guard:
            if ingest_key in self._ingesting:
                raise IngestionInProgressError()
            self._ingesting.add(ingest_key)
        try:
            return await self._ingest_locked(
                path, original_filename, content, document_id, tenant_id
            )
        finally:
            async with self._ingest_guard:
                self._ingesting.discard(ingest_key)

    async def _ingest_locked(
        self,
        path: Path,
        original_filename: str,
        content: bytes,
        document_id: str,
        tenant_id: str,
    ) -> UploadResponse:
        started = time.perf_counter()

        # pypdf e o splitter sao sincronos e pesados: fora do event loop.
        extraction = await asyncio.to_thread(
            extract_document,
            path,
            min_chars_per_page=self.options.min_chars_per_page,
            min_extraction_ratio=self.options.min_extraction_ratio,
        )

        confirmed: dict[str, Any] = {}
        if self.upload_path is not None:
            confirmed = await asyncio.to_thread(
                load_sidecar, sidecar_path(self.upload_path, original_filename)
            )

        head = "\n".join(page.text for page in extraction.pages[:3])
        metadata = extract_metadata(
            head,
            document_id=document_id,
            filename=original_filename,
            raw_content=content,
            confirmed=confirmed,
        )

        chunks = await asyncio.to_thread(
            build_chunks,
            [(page.number, page.text) for page in extraction.pages],
            metadata,
            tenant_id=tenant_id,
            chunk_chars=self.options.chunk_chars,
            overlap_chars=self.options.chunk_overlap_chars,
        )
        if not chunks:
            raise EmptyDocumentError()

        texts = [chunk.text for chunk in chunks]
        embeddings: list[list[float]] = []
        batch = self.options.embed_batch_size
        embed_started = time.perf_counter()
        for start in range(0, len(texts), batch):
            embeddings.extend(await self.ollama.embed(texts[start : start + batch]))
        embed_ms = int((time.perf_counter() - embed_started) * 1000)

        previous_ids = await asyncio.to_thread(
            self.store.chunk_ids_for_document, tenant_id, document_id
        )
        new_ids = [
            f"{tenant_id}:{document_id}:{index}"
            for index in range(1, len(chunks) + 1)
        ]

        # Grava primeiro. Se falhar aqui, nada foi removido.
        await asyncio.to_thread(
            self.store.upsert,
            tenant_id,
            new_ids,
            texts,
            embeddings,
            [chunk.metadata for chunk in chunks],
        )

        stale = [chunk_id for chunk_id in previous_ids if chunk_id not in set(new_ids)]
        if stale:
            await asyncio.to_thread(self.store.delete_ids, tenant_id, stale)

        if self.knowledge_graph is not None:
            await self.knowledge_graph.sync_document(tenant_id, metadata, head)

        await self._refresh_bm25(tenant_id)

        logger.info(
            "document_indexed",
            extra={
                "document_id": document_id,
                "document_filename": original_filename,
                "total_pages": extraction.total_pages,
                "pages_with_text": extraction.pages_with_text,
                "total_chars": extraction.total_chars,
                "chunks": len(chunks),
                "replaced_chunks": len(stale),
                "authority": metadata.authority,
                "regulation_number": metadata.regulation_number,
                "status": metadata.status,
                "embed_ms": embed_ms,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return UploadResponse(
            document_id=document_id,
            filename=original_filename,
            pages=extraction.pages_with_text,
            total_pages=extraction.total_pages,
            chunks=len(chunks),
            chars_per_page=extraction.chars_per_page,
            authority=metadata.authority,
            regulation_number=metadata.regulation_number,
            status=metadata.status,
            status_label=status_label(metadata.status),
        )

    # ------------------------------------------------------------------
    # Recuperacao
    # ------------------------------------------------------------------

    @staticmethod
    def _relation_payload(relation: KagRelation) -> dict[str, Any]:
        return {
            "id": relation.id,
            "relation": relation.relation,
            "subject": relation.subject,
            "subject_kind": relation.subject_kind,
            "object": relation.object,
            "object_kind": relation.object_kind,
            "source_reference": relation.source_reference,
            "source_document_id": relation.source_document_id,
            "validation_status": relation.validation_status,
            "extracted_by": relation.extracted_by,
            "confidence": relation.confidence,
            "source_span": relation.source_span,
            "validated_by": relation.validated_by,
            "validated_at": (
                relation.validated_at.isoformat()
                if relation.validated_at is not None
                else None
            ),
        }

    def _to_source(self, candidate: Candidate, cited: bool) -> Source:
        metadata = candidate.metadata
        return Source(
            document=str(metadata.get("filename", "documento")),
            page=metadata.get("page"),
            chunk=int(metadata.get("chunk", 0) or 0),
            score=round(candidate.vector_score, 4),
            lexical_score=round(candidate.lexical_score, 4),
            excerpt=candidate.text[:600],
            authority=metadata.get("authority"),
            regulation_number=metadata.get("regulation_number"),
            publication_date=metadata.get("publication_date"),
            effective_date=metadata.get("effective_date"),
            status=metadata.get("status"),
            status_label=status_label(metadata.get("status")),
            article=metadata.get("article"),
            section=metadata.get("section"),
            source_url=metadata.get("source_url"),
            document_version=metadata.get("document_version"),
            cited=cited,
        )

    async def retrieve(
        self, question: str, *, tenant_id: str = LOCAL_TENANT_ID
    ) -> RetrievalOutcome:
        """Executa a recuperacao hibrida sem chamar o modelo de chat.

        Retorno antecipado: se a colecao estiver vazia, nenhum embedding e
        gerado e o modelo de embeddings nem chega a ser carregado.
        """
        outcome = RetrievalOutcome()
        outcome.collection_count = await asyncio.to_thread(
            self.store.count_chunks, tenant_id
        )

        logger.info(
            "chat_start",
            extra={
                "question_len": len(question),
                "collection_count": outcome.collection_count,
                "embedding_model": self.ollama.embedding_model,
                "chat_model": self.ollama.chat_model,
            },
        )

        if outcome.collection_count == 0:
            logger.info("chat_empty_collection_early_return")
            outcome.early_exit_answer = EMPTY_BASE_ANSWER
            return outcome

        embed_started = time.perf_counter()
        embeddings = await self.ollama.embed([question])
        embed_elapsed = time.perf_counter() - embed_started
        outcome.embed_ms = int(embed_elapsed * 1000)
        prom_metrics.observe_stage("embed", embed_elapsed)

        # Vector + BM25 + KAG são independentes: rodam em paralelo. O event
        # loop libera a thread do embedder e o Postgres do KAG enquanto o
        # Chroma responde, então o tempo de recuperação passa a ser o do
        # ramo mais lento — e não a soma dos três.
        tenant_bm25 = self._bm25(tenant_id)
        run_bm25 = self.options.hybrid_search_enabled and tenant_bm25.size > 0
        run_graph = self.knowledge_graph is not None

        query_started = time.perf_counter()

        async def _vector_task() -> list[dict[str, Any]]:
            return await asyncio.to_thread(
                self.store.query,
                tenant_id,
                embeddings[0],
                self.options.retrieval_candidates,
            )

        async def _bm25_task() -> list[Any]:
            if not run_bm25:
                return []
            return await asyncio.to_thread(
                tenant_bm25.search, question, self.options.retrieval_candidates
            )

        async def _graph_task() -> list[KagRelation]:
            if not run_graph:
                return []
            assert self.knowledge_graph is not None
            return await self.knowledge_graph.search_context(tenant_id, question)

        vector_results, bm25_hits, graph_relations = await asyncio.gather(
            _vector_task(), _bm25_task(), _graph_task()
        )
        stage_elapsed = time.perf_counter() - query_started
        stage_ms = int(stage_elapsed * 1000)
        outcome.vector_query_ms = stage_ms
        outcome.bm25_query_ms = stage_ms if run_bm25 else 0
        outcome.graph_query_ms = stage_ms if run_graph else 0
        prom_metrics.observe_stage("vector", stage_elapsed)
        if run_bm25:
            prom_metrics.observe_stage("bm25", stage_elapsed)
        if run_graph:
            prom_metrics.observe_stage("graph", stage_elapsed)

        lexical_documents: dict[str, dict[str, Any]] = {}
        if run_bm25 and bm25_hits:
            known = {str(item.get("id")) for item in vector_results}
            missing = [hit.chunk_id for hit in bm25_hits if hit.chunk_id not in known]
            if missing:
                lexical_documents = await asyncio.to_thread(
                    self.store.get_chunks, tenant_id, missing
                )

        fusion_started = time.perf_counter()
        fused = fuse(
            vector_results,
            bm25_hits,
            lexical_documents,
            question=question,
            rrf_k=self.options.rrf_k,
        )
        outcome.selected = select_context(
            fused,
            min_relevance_score=self.options.min_relevance_score,
            max_context_chunks=self.options.max_context_chunks,
            dedup_similarity=self.options.dedup_similarity,
        )
        outcome.fusion_ms = int((time.perf_counter() - fusion_started) * 1000)
        outcome.fused_count = len(fused)
        outcome.scores = [round(candidate.vector_score, 3) for candidate in fused[:10]]

        logger.info(
            "chat_retrieval",
            extra={
                "collection_count": outcome.collection_count,
                "vector_candidates": len(vector_results),
                "bm25_candidates": len(bm25_hits),
                "fused_candidates": outcome.fused_count,
                "selected_chunks": len(outcome.selected),
                "scores": outcome.scores,
                "embed_ms": outcome.embed_ms,
                "vector_query_ms": outcome.vector_query_ms,
                "bm25_query_ms": outcome.bm25_query_ms,
                "fusion_ms": outcome.fusion_ms,
            },
        )

        if not outcome.selected:
            outcome.early_exit_answer = NO_EVIDENCE_ANSWER
            return outcome

        outcome.sources = [
            self._to_source(candidate, cited=False) for candidate in outcome.selected
        ]
        outcome.context_blocks = [
            f"[Fonte {index}]\n{candidate.text}"
            for index, candidate in enumerate(outcome.selected, start=1)
        ]
        outcome.knowledge_relations = list(graph_relations)
        if outcome.knowledge_relations:
            outcome.context_blocks.append(
                "[Relações KAG — orientação; confirme sempre pelas Fontes]\n"
                + "\n".join(
                    rel.as_prompt_line() for rel in outcome.knowledge_relations
                )
            )
        return outcome

    def _audit(self, answer_text: str, outcome: RetrievalOutcome) -> CitationAudit:
        return audit_answer(
            answer_text,
            [
                {
                    "score": candidate.vector_score,
                    "metadata": candidate.metadata,
                }
                for candidate in outcome.selected
            ],
            min_relevance_score=self.options.min_relevance_score,
        )

    @staticmethod
    def _final_answer(generated: str, audit: CitationAudit) -> str:
        """Texto que chega ao usuario depois da auditoria de citacoes.

        Uma resposta sem nenhuma citacao valida nao e verificavel e nao pode
        ser apresentada como se fosse: e a via por onde uma instrucao injetada
        num documento chegaria ao usuario travestida de conteudo normativo.
        Nesse caso a prosa gerada e descartada em favor da recusa padrao.
        """
        if audit.has_valid_citation:
            return generated
        return NO_EVIDENCE_ANSWER

    @staticmethod
    def _cited_sources(
        outcome: RetrievalOutcome, audit: CitationAudit
    ) -> list[Source]:
        """Apenas as fontes efetivamente citadas, marcadas como tal."""
        cited: list[Source] = []
        for index in audit.cited_indexes:
            source = outcome.sources[index - 1].model_copy(update={"cited": True})
            cited.append(source)
        return cited

    def _early_response(self, outcome: RetrievalOutcome, started: float) -> ChatResponse:
        return ChatResponse(
            answer=outcome.early_exit_answer or NO_EVIDENCE_ANSWER,
            sources=[],
            grounded=False,
            requires_human_review=True,
            disclaimer=DISCLAIMER,
            request_id=get_request_id(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            collection_count=outcome.collection_count,
            embed_ms=outcome.embed_ms,
            retrieval_ms=outcome.vector_query_ms + outcome.bm25_query_ms,
        )

    # ------------------------------------------------------------------
    # Resposta JSON (compatibilidade preservada)
    # ------------------------------------------------------------------

    async def answer(
        self, question: str, *, tenant_id: str = LOCAL_TENANT_ID
    ) -> ChatResponse:
        """Recupera trechos relevantes e gera resposta fundamentada."""
        started = time.perf_counter()
        outcome = await self.retrieve(question, tenant_id=tenant_id)

        if outcome.early_exit_answer is not None:
            logger.info(
                "chat_completed",
                extra={
                    "grounded": False,
                    "early_exit": True,
                    "chat_called": False,
                    "collection_count": outcome.collection_count,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            return self._early_response(outcome, started)

        chat_started = time.perf_counter()
        result = await self.ollama.chat(
            build_system_prompt(),
            build_user_prompt(outcome.context_blocks, question),
        )
        chat_elapsed = time.perf_counter() - chat_started
        chat_ms = int(chat_elapsed * 1000)
        prom_metrics.observe_stage("chat", chat_elapsed)
        prom_metrics.observe_chat_metrics(
            tokens_per_second=result.metrics.tokens_per_second,
            eval_count=result.metrics.eval_count,
        )

        audit = self._audit(result.content, outcome)
        prom_metrics.observe_answer(self._outcome_label(outcome, audit))
        duration_ms = int((time.perf_counter() - started) * 1000)
        self._log_completion(outcome, audit, chat_ms, duration_ms, result.metrics)

        return ChatResponse(
            answer=self._final_answer(result.content, audit),
            sources=self._cited_sources(outcome, audit),
            retrieved_sources=outcome.sources,
            relations=[
                KagRelationView(**self._relation_payload(rel))
                for rel in outcome.knowledge_relations
            ],
            grounded=audit.grounded,
            requires_human_review=audit.requires_human_review,
            review_reasons=audit.reasons,
            disclaimer=DISCLAIMER,
            request_id=get_request_id(),
            duration_ms=duration_ms,
            collection_count=outcome.collection_count,
            embed_ms=outcome.embed_ms,
            retrieval_ms=outcome.vector_query_ms + outcome.bm25_query_ms,
            graph_ms=outcome.graph_query_ms,
            chat_ms=chat_ms,
            load_duration_ms=result.metrics.load_duration_ms,
            eval_count=result.metrics.eval_count,
            tokens_per_second=result.metrics.tokens_per_second,
        )

    # ------------------------------------------------------------------
    # Resposta em streaming (NDJSON)
    # ------------------------------------------------------------------

    async def answer_stream(
        self, question: str, *, tenant_id: str = LOCAL_TENANT_ID
    ) -> AsyncIterator[dict[str, Any]]:
        """Emite eventos NDJSON: metadata, token, done."""
        started = time.perf_counter()
        request_id = get_request_id()
        outcome = await self.retrieve(question, tenant_id=tenant_id)

        if outcome.early_exit_answer is not None:
            logger.info(
                "chat_stream_completed",
                extra={
                    "grounded": False,
                    "early_exit": True,
                    "chat_called": False,
                    "collection_count": outcome.collection_count,
                },
            )
            yield {"type": "metadata", "request_id": request_id, "sources": []}
            yield {"type": "token", "content": outcome.early_exit_answer}
            yield {
                "type": "done",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "grounded": False,
                "requires_human_review": True,
                "review_reasons": ["Nenhum contexto adequado foi recuperado."],
                "sources": [],
                "disclaimer": DISCLAIMER,
            }
            return

        yield {
            "type": "metadata",
            "request_id": request_id,
            "sources": [source.model_dump() for source in outcome.sources],
            "relations": [
                self._relation_payload(rel) for rel in outcome.knowledge_relations
            ],
        }

        chat_started = time.perf_counter()
        pieces: list[str] = []
        metrics = OllamaMetrics()
        async for event in self.ollama.chat_stream(
            build_system_prompt(),
            build_user_prompt(outcome.context_blocks, question),
        ):
            if event.content:
                pieces.append(event.content)
                yield {"type": "token", "content": event.content}
            if event.done and event.metrics is not None:
                metrics = event.metrics

        chat_elapsed = time.perf_counter() - chat_started
        chat_ms = int(chat_elapsed * 1000)
        prom_metrics.observe_stage("chat", chat_elapsed)
        prom_metrics.observe_chat_metrics(
            tokens_per_second=metrics.tokens_per_second,
            eval_count=metrics.eval_count,
        )
        answer_text = "".join(pieces).strip()
        audit = self._audit(answer_text, outcome)
        prom_metrics.observe_answer(self._outcome_label(outcome, audit))
        duration_ms = int((time.perf_counter() - started) * 1000)
        self._log_completion(outcome, audit, chat_ms, duration_ms, metrics)

        yield {
            "type": "done",
            "duration_ms": duration_ms,
            "grounded": audit.grounded,
            "requires_human_review": audit.requires_human_review,
            "review_reasons": audit.reasons,
            # Texto canonico pos-auditoria: o frontend substitui o que
            # streamou, porque uma resposta sem citacao valida e descartada.
            "answer": self._final_answer(answer_text, audit),
            "sources": [
                source.model_dump()
                for source in self._cited_sources(outcome, audit)
            ],
            "disclaimer": DISCLAIMER,
            "chat_ms": chat_ms,
            "load_duration_ms": metrics.load_duration_ms,
            "tokens_per_second": metrics.tokens_per_second,
        }

    # ------------------------------------------------------------------
    # Observabilidade e consultas auxiliares
    # ------------------------------------------------------------------

    @staticmethod
    def _outcome_label(outcome: RetrievalOutcome, audit: CitationAudit) -> str:
        """Classifica o resultado para o counter Prometheus."""
        if outcome.collection_count == 0:
            return "no_context"
        if not outcome.selected:
            return "no_context"
        if audit.grounded and not audit.requires_human_review:
            return "grounded"
        return "needs_review"

    def _log_completion(
        self,
        outcome: RetrievalOutcome,
        audit: CitationAudit,
        chat_ms: int,
        duration_ms: int,
        metrics: OllamaMetrics,
    ) -> None:
        logger.info(
            "chat_completed",
            extra={
                "collection_count": outcome.collection_count,
                "embedding_model": self.ollama.embedding_model,
                "chat_model": self.ollama.chat_model,
                "embed_ms": outcome.embed_ms,
                "vector_query_ms": outcome.vector_query_ms,
                "bm25_query_ms": outcome.bm25_query_ms,
                "fusion_ms": outcome.fusion_ms,
                "chat_ms": chat_ms,
                "duration_ms": duration_ms,
                "candidates": outcome.fused_count,
                "context_chunks": len(outcome.selected),
                "scores": outcome.scores,
                "grounded": audit.grounded,
                "valid_citations": audit.cited_indexes,
                "invalid_citations": audit.invalid_indexes,
                "requires_human_review": audit.requires_human_review,
                "hybrid": self.options.hybrid_search_enabled,
                "bm25_index_size": self.bm25.size,
                **metrics.as_log_fields(),
            },
        )

    def documents(self, tenant_id: str = LOCAL_TENANT_ID) -> list[DocumentSummary]:
        """Lista os documentos indexados."""
        return [
            DocumentSummary(
                document_id=str(item.get("document_id", "")),
                filename=str(item.get("filename", "documento")),
                chunks=int(item.get("chunks", 0)),
                authority=item.get("authority"),
                regulation_number=item.get("regulation_number"),
                status=item.get("status"),
                status_label=status_label(item.get("status")),
                publication_date=item.get("publication_date"),
            )
            for item in self.store.list_documents(tenant_id)
        ]

    def unverified_documents(self, tenant_id: str = LOCAL_TENANT_ID) -> int:
        """Quantos documentos estao sem vigencia confirmada."""
        return sum(
            1
            for item in self.store.list_documents(tenant_id)
            if (item.get("status") or "") != STATUS_IN_FORCE
        )

    async def update_document_status(
        self,
        document_id: str,
        status: str,
        *,
        tenant_id: str = LOCAL_TENANT_ID,
        validated_by: str,
    ) -> tuple[int, datetime | None]:
        """Registra a decisao humana sem recalcular embeddings."""
        if status not in VALID_STATUSES:
            raise ValueError("status regulatorio invalido")

        validation_time = (
            None if status == STATUS_UNVERIFIED else datetime.now(timezone.utc)
        )
        updated = await asyncio.to_thread(
            self.store.update_document_status,
            tenant_id,
            document_id,
            status=status,
            status_label=status_label(status),
            validated_by=None if validation_time is None else validated_by,
            validated_at=(
                None if validation_time is None else validation_time.isoformat()
            ),
        )
        if updated:
            await self._refresh_bm25(tenant_id)
        return updated, validation_time

    async def delete_document(
        self, document_id: str, *, tenant_id: str = LOCAL_TENANT_ID
    ) -> bool:
        """Exclui somente o documento pertencente ao tenant autenticado.

        Ordem importa: primeiro o KAG (para o grafo nunca apontar para chunks
        órfãos), depois o vetor, depois o BM25. Se a limpeza do KAG falhar,
        a chamada aborta e o documento continua indexado — melhor manter
        tudo coerente que remover a metade visível para o usuário.
        """
        existing = await asyncio.to_thread(
            self.store.chunk_ids_for_document, tenant_id, document_id
        )
        if not existing:
            return False

        removed_relations = 0
        if self.knowledge_graph is not None:
            removed_relations = await self.knowledge_graph.forget_document(
                tenant_id, document_id
            )

        await asyncio.to_thread(
            self.store.delete_document, tenant_id, document_id
        )
        await self._refresh_bm25(tenant_id)

        logger.info(
            "document_deleted",
            extra={
                "document_id": document_id,
                "tenant_id": tenant_id,
                "chunks_removed": len(existing),
                "kag_relations_removed": removed_relations,
            },
        )
        return True
