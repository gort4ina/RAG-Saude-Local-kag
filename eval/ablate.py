"""Harness de ablacao da pipeline de retrieval.

Fachada CLI sobre ``app.harness.evaluation``. Isola a contribuicao de cada
camada:

    V      = somente busca vetorial (ChromaDB)
    V+B    = vetor + BM25 fundidos por RRF
    V+B+K  = vetor + BM25 + KnowledgeGraph

Uso tipico::

    python -m eval.ablate --tenant local

    python -m eval.ablate --tenant local --output eval/reports/ablation.json \\
        --variants vector,hybrid,hybrid_kag
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.harness.evaluation.dataset import (
    cases_as_raw_dicts,
    load_evaluation_cases,
)
from app.harness.evaluation.reporter import write_report
from app.harness.evaluation.runner import (
    VARIANT_LABELS,
    compare_variant_reports,
    evaluate_variant,
    options_snapshot,
)
from app.harness.schemas import CaseOutcome, VariantReport
from app.services.normalize import normalize_text
from app.services.ollama_client import OllamaClient
from app.services.rag_service import LOCAL_TENANT_ID, RagOptions
from app.services.vector_store import VectorStore

# Re-exportes para compatibilidade com ``tests/test_ablate.py``.
_normalize = normalize_text
vars_of = options_snapshot
_compare = compare_variant_reports

__all__ = [
    "CaseOutcome",
    "VariantReport",
    "VARIANT_LABELS",
    "_compare",
    "_normalize",
    "main",
    "vars_of",
]

DATASET_PATH = Path(__file__).with_name("dataset.json")


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(argv))


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

    loaded = load_evaluation_cases(args.dataset)
    cases = cases_as_raw_dicts(loaded)
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
        graph_in_rrf=settings.graph_in_rrf,
        reranker_enabled=settings.reranker_enabled,
        query_router_enabled=settings.query_router_enabled,
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

    kag_store = None
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
            report = await evaluate_variant(
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

    summary: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tenant_id": args.tenant,
        "variants": [report.summary() for report in reports],
        "comparison": compare_variant_reports(reports),
        "options": options_snapshot(options),
        "measured": True,
    }

    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))

    if args.output:
        write_report(
            {
                "summary": summary,
                "cases_per_variant": {
                    report.name: [c.to_dict() for c in report.cases]
                    for report in reports
                },
            },
            args.output,
            measured=True,
            harness_mode="evaluation",
        )
        print(f"\nRelatorio salvo em {args.output}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
