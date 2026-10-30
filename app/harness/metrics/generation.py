"""Metricas de geracao / resposta (agregacao sobre resultados HTTP).

Nao chama LLM. Opera sobre campos ja coletados pelo runner E2E
(``eval.run_eval``): grounded, abstained, cited_sources, etc.
"""

from __future__ import annotations

import statistics
from typing import Any, Protocol, Sequence


class GenerationCaseLike(Protocol):
    should_answer: bool
    http_status: int
    grounded: bool
    abstained: bool
    cited_sources: int
    document_hit_at_5: bool | None
    keyword_coverage: float | None
    duration_ms: int
    embed_ms: int
    chat_ms: int
    load_duration_ms: int
    tokens_per_second: float


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * percentile
    lower = ordered[int(rank)]
    upper = ordered[min(int(rank) + 1, len(ordered) - 1)]
    return round(lower + (upper - lower) * (rank - int(rank)), 2)


def grounded_rate(results: Sequence[GenerationCaseLike]) -> float | None:
    answerable = [
        item for item in results if item.should_answer and item.http_status == 200
    ]
    if not answerable:
        return None
    return round(sum(1 for item in answerable if item.grounded) / len(answerable), 3)


def citation_precision(results: Sequence[GenerationCaseLike]) -> float | None:
    """Entre respostas grounded, fracao com pelo menos uma fonte citada."""
    cited_pool = [
        item
        for item in results
        if item.should_answer and item.http_status == 200 and item.grounded
    ]
    if not cited_pool:
        return None
    return round(
        sum(1 for item in cited_pool if item.cited_sources > 0) / len(cited_pool),
        3,
    )


def abstention_correctness(results: Sequence[GenerationCaseLike]) -> float | None:
    """Entre casos que nao deveriam responder, fracao que se abstiveram."""
    refusable = [item for item in results if not item.should_answer]
    if not refusable:
        return None
    refused = [
        item
        for item in refusable
        if item.http_status == 200 and (item.abstained or not item.grounded)
    ]
    return round(len(refused) / len(refusable), 3)


def summarize_generation(results: Sequence[GenerationCaseLike]) -> dict[str, Any]:
    """Agrega qualidade e latencia no formato usado por ``eval.run_eval``."""
    answerable = [item for item in results if item.should_answer]
    successful = [item for item in results if item.http_status == 200]

    with_gold = [item for item in answerable if item.document_hit_at_5 is not None]
    recall_at_5 = (
        round(sum(1 for item in with_gold if item.document_hit_at_5) / len(with_gold), 3)
        if with_gold
        else None
    )

    coverages = [
        item.keyword_coverage
        for item in answerable
        if item.keyword_coverage is not None
    ]
    keyword_coverage = (
        round(statistics.mean(coverages), 3) if coverages else None
    )

    latencies = [float(item.duration_ms) for item in successful]
    embed_times = [float(item.embed_ms) for item in successful if item.embed_ms]
    load_times = [
        float(item.load_duration_ms) for item in successful if item.load_duration_ms
    ]
    chat_times = [float(item.chat_ms) for item in successful if item.chat_ms]
    throughput = [
        item.tokens_per_second for item in successful if item.tokens_per_second
    ]

    return {
        "cases_total": len(results),
        "cases_ok": len(successful),
        "cases_failed": len(results) - len(successful),
        "quality": {
            "recall_at_5": recall_at_5,
            "recall_at_5_evaluated_cases": len(with_gold),
            "citation_precision": citation_precision(results),
            "grounded_rate": grounded_rate(results),
            "abstention_accuracy": abstention_correctness(results),
            "keyword_coverage": keyword_coverage,
        },
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "mean": round(statistics.mean(latencies), 2) if latencies else None,
            "embedding_mean": (
                round(statistics.mean(embed_times), 2) if embed_times else None
            ),
            "model_load_mean": (
                round(statistics.mean(load_times), 2) if load_times else None
            ),
            "generation_mean": (
                round(statistics.mean(chat_times), 2) if chat_times else None
            ),
        },
        "tokens_per_second_mean": (
            round(statistics.mean(throughput), 2) if throughput else None
        ),
        "notes": [
            "recall_at_5 e None quando nenhum caso tem 'expected_document' preenchido.",
            "abstention_accuracy conta como acerto tanto a frase de recusa quanto grounded=false.",
            "Todas as latencias vem dos campos devolvidos pela propria API.",
            "measured=true: valores derivados de execucoes reais, nao simuladas.",
        ],
        "measured": True,
    }
