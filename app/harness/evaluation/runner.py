"""Runner offline de retrieval (ablacao V / V+B / V+B+K).

Nao chama o modelo de chat. Depende de embeddings + store ja indexado.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.harness.metrics.retrieval import compare_variants, mrr
from app.harness.schemas import CaseOutcome, VariantReport
from app.services.bm25_index import Bm25Index
from app.services.graph_store import GraphStore
from app.services.normalize import normalize_text
from app.services.ollama_client import OllamaClient
from app.services.rag_service import RagOptions, RagService
from app.services.vector_port import VectorStorePort

VARIANT_LABELS = {
    "vector": "V (vector only)",
    "hybrid": "V+B (vector+BM25)",
    "hybrid_kag": "V+B+K (vector+BM25+KAG)",
}


def options_snapshot(options: RagOptions) -> dict[str, Any]:
    return {
        "retrieval_candidates": options.retrieval_candidates,
        "max_context_chunks": options.max_context_chunks,
        "min_relevance_score": options.min_relevance_score,
        "chunk_chars": options.chunk_chars,
        "chunk_overlap_chars": options.chunk_overlap_chars,
        "embed_batch_size": options.embed_batch_size,
        "hybrid_search_enabled": options.hybrid_search_enabled,
        "rrf_k": options.rrf_k,
        "dedup_similarity": options.dedup_similarity,
        "min_chars_per_page": options.min_chars_per_page,
        "min_extraction_ratio": options.min_extraction_ratio,
        "graph_in_rrf": options.graph_in_rrf,
        "reranker_enabled": options.reranker_enabled,
        "query_router_enabled": options.query_router_enabled,
    }


def build_service_for_variant(
    *,
    variant: str,
    ollama: OllamaClient,
    store: VectorStorePort,
    options: RagOptions,
    kag_store: GraphStore | None,
) -> RagService:
    """Monta um RagService com as flags corretas para a variante."""
    snapshot = options_snapshot(options)
    if variant == "vector":
        opts = RagOptions(**{**snapshot, "hybrid_search_enabled": False})
        graph: GraphStore | None = None
    elif variant == "hybrid":
        opts = RagOptions(**{**snapshot, "hybrid_search_enabled": True})
        graph = None
    elif variant == "hybrid_kag":
        opts = RagOptions(**{**snapshot, "hybrid_search_enabled": True})
        graph = kag_store
    else:
        raise ValueError(f"Variante desconhecida: {variant}")

    return RagService(
        ollama=ollama,
        store=store,
        options=opts,
        bm25=Bm25Index(),
        knowledge_graph=graph,
    )


async def _warmup_bm25(service: RagService, tenant_id: str) -> None:
    chunks = await asyncio.to_thread(service.store.all_chunks, tenant_id)
    service._bm25(tenant_id).rebuild(chunks)  # type: ignore[attr-defined]


async def evaluate_variant(
    *,
    variant: str,
    cases: list[dict[str, Any]],
    tenant_id: str,
    ollama: OllamaClient,
    store: VectorStorePort,
    options: RagOptions,
    kag_store: GraphStore | None,
    progress: bool = True,
) -> VariantReport:
    if variant not in VARIANT_LABELS:
        raise ValueError(f"Variante desconhecida: {variant}")

    service = build_service_for_variant(
        variant=variant,
        ollama=ollama,
        store=store,
        options=options,
        kag_store=kag_store,
    )
    if variant != "vector":
        await _warmup_bm25(service, tenant_id)

    report = VariantReport(name=variant, label=VARIANT_LABELS[variant])
    for index, raw in enumerate(cases, start=1):
        case = CaseOutcome(
            id=str(raw.get("id", "")),
            question=str(raw["question"]),
            should_answer=bool(raw.get("should_answer", True)),
            expected_document=raw.get("expected_document") or None,
        )
        started = time.perf_counter()
        try:
            outcome = await service.retrieve(case.question, tenant_id=tenant_id)
        except Exception as exc:  # pragma: no cover - integra Ollama real
            case.error = f"{type(exc).__name__}: {exc}"
            case.retrieval_ms = int((time.perf_counter() - started) * 1000)
            report.cases.append(case)
            if progress:
                print(
                    f"  [{index:>3}/{len(cases)}] ERR  {case.id:<14} {case.error}"
                )
            continue

        case.retrieval_ms = int((time.perf_counter() - started) * 1000)
        case.embed_ms = outcome.embed_ms
        case.vector_query_ms = outcome.vector_query_ms
        case.bm25_query_ms = outcome.bm25_query_ms
        case.graph_query_ms = outcome.graph_query_ms
        case.fusion_ms = outcome.fusion_ms
        case.fused_count = outcome.fused_count
        case.selected_count = len(outcome.selected)
        case.retrieved_documents = [
            str(src.document) for src in outcome.sources
        ]
        case.retrieved_chunk_ids = [
            candidate.chunk_id for candidate in outcome.selected
        ]

        gold = []
        if case.expected_document:
            gold = [case.expected_document]
        elif raw.get("expected_document_ids"):
            gold = [str(item) for item in raw["expected_document_ids"]]

        if gold:
            expected = normalize_text(gold[0])
            case.document_hit_at_5 = any(
                expected in normalize_text(doc)
                for doc in case.retrieved_documents[:5]
            )
            case.reciprocal_rank = mrr(case.retrieved_documents, gold, k=5)

        hit = (
            "hit "
            if case.document_hit_at_5
            else ("miss" if case.document_hit_at_5 is False else "n/a ")
        )
        if progress:
            print(
                f"  [{index:>3}/{len(cases)}] {hit} {case.id:<14} "
                f"sel={case.selected_count}/{case.fused_count} "
                f"{case.retrieval_ms}ms"
            )
        report.cases.append(case)

    return report


def compare_variant_reports(reports: list[VariantReport]) -> dict[str, Any]:
    return compare_variants(reports)
