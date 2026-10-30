"""Metricas de recuperacao (deterministicas, sem LLM).

Todas as funcoes abaixo operam sobre listas de identificadores ja
normalizados (filenames ou document_ids). Relevancia e binaria: o item
gold esta ou nao na lista recuperada.
"""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from app.services.normalize import normalize_text

if TYPE_CHECKING:
    from app.harness.schemas import CaseOutcome, VariantReport


def _matches_gold(candidate: str, gold: str) -> bool:
    return normalize_text(gold) in normalize_text(candidate)


def _hit_positions(
    retrieved: Sequence[str], gold_items: Sequence[str], *, k: int
) -> list[int]:
    """Posicoes 1-indexed (ate k) em que algum gold aparece."""
    positions: list[int] = []
    top = list(retrieved[:k])
    for index, item in enumerate(top, start=1):
        if any(_matches_gold(item, gold) for gold in gold_items):
            positions.append(index)
    return positions


def recall_at_k(
    retrieved: Sequence[str], gold_items: Sequence[str], *, k: int = 5
) -> float | None:
    """Fracao dos golds encontrados no top-k. None se nao houver gold."""
    if not gold_items:
        return None
    found = 0
    top = list(retrieved[:k])
    for gold in gold_items:
        if any(_matches_gold(item, gold) for item in top):
            found += 1
    return round(found / len(gold_items), 6)


def precision_at_k(
    retrieved: Sequence[str], gold_items: Sequence[str], *, k: int = 5
) -> float | None:
    """Fracao dos top-k que batem com algum gold. None se top-k vazio ou sem gold."""
    if not gold_items:
        return None
    top = list(retrieved[:k])
    if not top:
        return 0.0
    hits = sum(
        1 for item in top if any(_matches_gold(item, gold) for gold in gold_items)
    )
    return round(hits / len(top), 6)


def hit_rate_at_k(
    retrieved: Sequence[str], gold_items: Sequence[str], *, k: int = 5
) -> float | None:
    """1.0 se pelo menos um gold esta no top-k; None sem gold."""
    if not gold_items:
        return None
    return 1.0 if _hit_positions(retrieved, gold_items, k=k) else 0.0


def mrr(
    retrieved: Sequence[str], gold_items: Sequence[str], *, k: int | None = None
) -> float | None:
    """Mean Reciprocal Rank do primeiro gold. None sem gold."""
    if not gold_items:
        return None
    limit = len(retrieved) if k is None else k
    positions = _hit_positions(retrieved, gold_items, k=limit)
    if not positions:
        return 0.0
    return round(1.0 / positions[0], 6)


def ndcg_at_k(
    retrieved: Sequence[str], gold_items: Sequence[str], *, k: int = 5
) -> float | None:
    """nDCG@k com relevancia binaria. None sem gold."""
    if not gold_items:
        return None
    top = list(retrieved[:k])
    dcg = 0.0
    for index, item in enumerate(top, start=1):
        relevant = any(_matches_gold(item, gold) for gold in gold_items)
        if relevant:
            dcg += 1.0 / math.log2(index + 1)
    ideal_hits = min(len(gold_items), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg == 0.0:
        return 0.0
    return round(dcg / idcg, 6)


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


def summarize_variant(report: VariantReport) -> dict[str, Any]:
    """Agrega Recall@5, latencias e contagens de uma VariantReport."""
    answerable = [c for c in report.cases if c.should_answer and c.error is None]
    with_gold = [c for c in answerable if c.document_hit_at_5 is not None]
    latencies = [float(c.retrieval_ms) for c in report.cases if c.error is None]
    embed_ms = [float(c.embed_ms) for c in report.cases if c.embed_ms]
    selected = [c.selected_count for c in answerable]

    mrr_values = [
        c.reciprocal_rank
        for c in answerable
        if c.reciprocal_rank is not None
    ]

    recall = (
        round(sum(1 for c in with_gold if c.document_hit_at_5) / len(with_gold), 3)
        if with_gold
        else None
    )
    return {
        "variant": report.name,
        "label": report.label,
        "cases_total": len(report.cases),
        "cases_errored": sum(1 for c in report.cases if c.error),
        "recall_at_5": recall,
        "recall_evaluated": len(with_gold),
        "mrr": round(statistics.mean(mrr_values), 3) if mrr_values else None,
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


def compare_variants(reports: Iterable[VariantReport]) -> dict[str, Any]:
    """Delta de Recall@5 entre variantes consecutivas."""
    report_list = list(reports)
    deltas: list[dict[str, Any]] = []
    for previous, current in zip(report_list, report_list[1:]):
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
