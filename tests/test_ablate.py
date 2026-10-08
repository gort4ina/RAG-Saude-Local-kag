"""Testes das funcoes puras do harness de ablacao."""

from __future__ import annotations

from eval.ablate import (
    CaseOutcome,
    VariantReport,
    _compare,
    _normalize,
    vars_of,
)
from app.services.rag_service import RagOptions


def test_normalize_strips_accents_and_lowercases() -> None:
    assert _normalize("Vigilância Sanitária") == "vigilancia sanitaria"
    assert _normalize("RDC 67/2007") == "rdc 67/2007"


def test_vars_of_contains_all_options() -> None:
    opts = RagOptions()
    data = vars_of(opts)
    assert data["retrieval_candidates"] == opts.retrieval_candidates
    assert data["chunk_chars"] == opts.chunk_chars
    assert set(data) >= {
        "retrieval_candidates",
        "max_context_chunks",
        "min_relevance_score",
        "chunk_chars",
        "chunk_overlap_chars",
        "hybrid_search_enabled",
        "rrf_k",
        "dedup_similarity",
    }


def _case(hit: bool | None, latency: int = 100) -> CaseOutcome:
    case = CaseOutcome(
        id="x",
        question="?",
        should_answer=True,
        expected_document="foo.md" if hit is not None else None,
    )
    case.document_hit_at_5 = hit
    case.retrieval_ms = latency
    case.selected_count = 2
    return case


def test_variant_report_summary_calcula_recall_e_latencia() -> None:
    report = VariantReport(name="vector", label="V")
    report.cases.extend(
        [
            _case(True, 100),
            _case(True, 150),
            _case(False, 200),
            _case(None, 50),  # sem gabarito nao conta
        ]
    )
    summary = report.summary()
    # 2 acertos em 3 casos COM gabarito.
    assert summary["recall_at_5"] == round(2 / 3, 3)
    assert summary["recall_evaluated"] == 3
    assert summary["cases_total"] == 4
    assert summary["retrieval_ms"]["p50"] == 125.0
    assert summary["retrieval_ms"]["mean"] == 125.0


def test_variant_report_summary_sem_gabarito_devolve_none() -> None:
    report = VariantReport(name="vector", label="V")
    report.cases.append(_case(None, 100))
    summary = report.summary()
    assert summary["recall_at_5"] is None


def test_compare_devolve_delta_entre_variantes() -> None:
    v1 = VariantReport("vector", "V")
    v1.cases.append(_case(True))
    v1.cases.append(_case(False))
    v2 = VariantReport("hybrid", "V+B")
    v2.cases.append(_case(True))
    v2.cases.append(_case(True))
    comparison = _compare([v1, v2])
    assert comparison["deltas"][0]["recall_delta"] == 0.5
    assert comparison["deltas"][0]["from"] == "vector"
    assert comparison["deltas"][0]["to"] == "hybrid"


def test_compare_ignora_variantes_sem_gabarito() -> None:
    v1 = VariantReport("vector", "V")
    v1.cases.append(_case(None))
    v2 = VariantReport("hybrid", "V+B")
    v2.cases.append(_case(True))
    comparison = _compare([v1, v2])
    assert comparison["deltas"][0]["recall_delta"] is None
