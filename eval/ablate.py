"""Harness de ablacao da pipeline de retrieval.

Objetivo
--------
Isolar a contribuicao de cada camada do retrieval:

    V      = somente busca vetorial (ChromaDB)
    V+B    = vetor + BM25 fundidos por RRF
    V+B+K  = vetor + BM25 + KnowledgeGraph (orientacao)

Para cada variante, roda o dataset de ``eval/dataset.json`` e devolve
Recall@5 e latencia de retrieval **sem** envolver o modelo de chat. Assim o
numero reflete a qualidade do retrieval puro, nao do estilo do modelo.

Pre-requisitos
--------------
- Ollama ligado com o modelo de embedding carregado (``settings.embedding_model``).
- ``knowledge_base/`` ja indexado no tenant alvo (use ``scripts.seed_knowledge_base``).

Uso tipico
----------
    python -m eval.ablate --tenant local

    python -m eval.ablate --tenant local --output eval/results/ablation.json \
        --variants vector,hybrid,hybrid_kag
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.bm25_index import Bm25Index
from app.services.graph_store import GraphStore
from app.services.ollama_client import OllamaClient
from app.services.rag_service import LOCAL_TENANT_ID, RagOptions, RagService
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = Path(__file__).with_name("dataset.json")

VARIANT_LABELS = {
    "vector": "V (vector only)",
    "hybrid": "V+B (vector+BM25)",
    "hybrid_kag": "V+B+K (vector+BM25+KAG)",
}


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


@dataclass(slots=True)
class CaseOutcome:
    id: str
    question: str
    should_answer: bool
    expected_document: str | None
    retrieved_documents: list[str] = field(default_factory=list)
    document_hit_at_5: bool | None = None
    retrieval_ms: int = 0
    embed_ms: int = 0
    vector_query_ms: int = 0
    bm25_query_ms: int = 0
    graph_query_ms: int = 0
    fusion_ms: int = 0
    fused_count: int = 0
    selected_count: int = 0
    error: str | None = None


@dataclass(slots=True)
class VariantReport:
    name: str
    label: str
    cases: list[CaseOutcome] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        answerable = [c for c in self.cases if c.should_answer and c.error is None]
        with_gold = [c for c in answerable if c.document_hit_at_5 is not None]
        latencies = [c.retrieval_ms for c in self.cases if c.error is None]
        embed_ms = [c.embed_ms for c in self.cases if c.embed_ms]
        selected = [c.selected_count for c in answerable]

        def _percentile(values: list[float], q: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            if len(ordered) == 1:
                return round(float(ordered[0]), 2)
            rank = (len(ordered) - 1) * q
            lower = ordered[int(rank)]
            upper = ordered[min(int(rank) + 1, len(ordered) - 1)]
            return round(float(lower + (upper - lower) * (rank - int(rank))), 2)

        recall = (
            round(sum(1 for c in with_gold if c.document_hit_at_5) / len(with_gold), 3)
            if with_gold
            else None
        )
        return {
            "variant": self.name,
            "label": self.label,
            "cases_total": len(self.cases),
            "cases_errored": sum(1 for c in self.cases if c.error),
            "recall_at_5": recall,
            "recall_evaluated": len(with_gold),
            "selected_mean": (
                round(statistics.mean(selected), 2) if selected else None
            ),
            "retrieval_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "mean": (
                    round(statistics.mean(latencies), 2) if latencies else None
                ),
            },
            "embed_ms_mean": (
                round(statistics.mean(embed_ms), 2) if embed_ms else None
            ),
        }


def _build_service(
    *,
    variant: str,
    ollama: OllamaClient,
    store: VectorStore,
    options: RagOptions,
    kag_store: GraphStore | None,
) -> RagService:
    """Monta um RagService com as flags corretas para a variante.

    Variacoes:
        - vector:     hybrid_search_enabled=False, sem KAG
        - hybrid:     hybrid_search_enabled=True,  sem KAG
        - hybrid_kag: hybrid_search_enabled=True,  com KAG
    """
    if variant == "vector":
        opts = RagOptions(**{**vars_of(options), "hybrid_search_enabled": False})
        graph: GraphStore | None = None
    elif variant == "hybrid":
        opts = RagOptions(**{**vars_of(options), "hybrid_search_enabled": True})
        graph = None
    elif variant == "hybrid_kag":
        opts = RagOptions(**{**vars_of(options), "hybrid_search_enabled": True})
        graph = kag_store
    else:
        raise ValueError(f"Variante desconhecida: {variant}")

    bm25 = Bm25Index()
    service = RagService(
        ollama=ollama,
        store=store,
        options=opts,
        bm25=bm25,
        knowledge_graph=graph,
    )
    return service


def vars_of(options: RagOptions) -> dict[str, Any]:
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


async def _warmup_bm25(service: RagService, tenant_id: str) -> None:
    """Rebuilda o BM25 em memoria a partir do Chroma, uma unica vez por run."""
    chunks = await asyncio.to_thread(service.store.all_chunks, tenant_id)
    service._bm25(tenant_id).rebuild(chunks)  # type: ignore[attr-defined]


async def _evaluate_variant(
    *,
    variant: str,
    cases: list[dict[str, Any]],
    tenant_id: str,
    ollama: OllamaClient,
    store: VectorStore,
    options: RagOptions,
    kag_store: GraphStore | None,
) -> VariantReport:
    service = _build_service(
        variant=variant,
        ollama=ollama,
        store=store,
        options=options,
        kag_store=kag_store,
    )
    if options.hybrid_search_enabled or variant != "vector":
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

        if case.expected_document:
            expected = _normalize(case.expected_document)
            case.document_hit_at_5 = any(
                expected in _normalize(doc)
                for doc in case.retrieved_documents[:5]
            )

        hit = (
            "hit "
            if case.document_hit_at_5
            else ("miss" if case.document_hit_at_5 is False else "n/a ")
        )
        print(
            f"  [{index:>3}/{len(cases)}] {hit} {case.id:<14} "
            f"sel={case.selected_count}/{case.fused_count} "
            f"{case.retrieval_ms}ms"
        )
        report.cases.append(case)

    return report


def _compare(reports: list[VariantReport]) -> dict[str, Any]:
    """Delta de Recall@5 entre variantes consecutivas."""
    deltas: list[dict[str, Any]] = []
    for previous, current in zip(reports, reports[1:]):
        prev_recall = previous.summary()["recall_at_5"]
        curr_recall = current.summary()["recall_at_5"]
        if prev_recall is None or curr_recall is None:
            delta = None
        else:
            delta = round(curr_recall - prev_recall, 3)
        deltas.append(
            {
                "from": previous.name,
                "to": current.name,
                "recall_delta": delta,
            }
        )
    return {"deltas": deltas}


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tenant",
        default=LOCAL_TENANT_ID,
        help="tenant_id a usar (padrao: local).",
    )
    parser.add_argument(
        "--variants",
        default="vector,hybrid,hybrid_kag",
        help="Variantes separadas por virgula. Opcoes: "
        + ",".join(VARIANT_LABELS),
    )
    parser.add_argument(
        "--dataset", type=Path, default=DATASET_PATH, help="Arquivo de casos."
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Arquivo JSON com o relatorio completo.",
    )
    args = parser.parse_args(argv)

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for name in variants:
        if name not in VARIANT_LABELS:
            print(f"Variante desconhecida: {name}", file=sys.stderr)
            return 2

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = dataset.get("cases") or []
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        print("Nenhum caso no dataset.", file=sys.stderr)
        return 1

    settings = get_settings()
    options = RagOptions(
        retrieval_candidates=settings.retrieval_candidates,
        max_context_chunks=settings.max_context_chunks,
        min_relevance_score=settings.min_relevance_score,
        chunk_chars=settings.chunk_chars,
        chunk_overlap_chars=settings.chunk_overlap_chars,
        embed_batch_size=settings.embed_batch_size,
        hybrid_search_enabled=settings.hybrid_search_enabled,
        rrf_k=settings.rrf_k,
        dedup_similarity=settings.dedup_similarity,
    )

    ollama = OllamaClient(
        base_url=settings.ollama_base_url,
        chat_model=settings.chat_model,
        embedding_model=settings.embedding_model,
        chat_timeout=settings.ollama_chat_timeout,
        embed_timeout=settings.ollama_embed_timeout,
        health_timeout=settings.ollama_health_timeout,
    )
    await ollama.startup()
    store = VectorStore(
        path=settings.chroma_path,
        collection_name=settings.collection_name,
        embedding_model=settings.embedding_model,
    )

    kag_store: GraphStore | None = None
    if "hybrid_kag" in variants and settings.kag_enabled:
        from app.dependencies import get_knowledge_graph_service

        try:
            kag_store = get_knowledge_graph_service()
        except Exception as exc:  # pragma: no cover - depende do Postgres real
            print(
                f"KAG indisponivel ({exc}); 'hybrid_kag' sera pulado.",
                file=sys.stderr,
            )
            variants = [v for v in variants if v != "hybrid_kag"]

    reports: list[VariantReport] = []
    try:
        for name in variants:
            print(f"\n== Rodando variante {name} ({VARIANT_LABELS[name]}) ==")
            report = await _evaluate_variant(
                variant=name,
                cases=cases,
                tenant_id=args.tenant,
                ollama=ollama,
                store=store,
                options=options,
                kag_store=kag_store,
            )
            reports.append(report)
    finally:
        await ollama.shutdown()

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tenant_id": args.tenant,
        "variants": [report.summary() for report in reports],
        "comparison": _compare(reports),
        "options": vars_of(options),
    }

    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        detailed = {
            "summary": summary,
            "cases_per_variant": {
                report.name: [vars(c) for c in report.cases]
                for report in reports
            },
        }
        args.output.write_text(
            json.dumps(detailed, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nRelatorio salvo em {args.output}")

    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
