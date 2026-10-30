"""Testes das metricas deterministicas do Evaluation Harness."""

from __future__ import annotations

from app.harness.metrics.generation import (
    abstention_correctness,
    citation_precision,
    grounded_rate,
    summarize_generation,
)
from app.harness.metrics.retrieval import (
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from app.harness.schemas import CaseOutcome, VariantReport


def test_recall_precision_hit_mrr_ndcg() -> None:
    retrieved = ["exemplo_rdc67_2007_manipulacao.md", "outro.md", "exemplo_afe.md"]
    gold = ["exemplo_rdc67_2007_manipulacao.md"]
    assert recall_at_k(retrieved, gold, k=5) == 1.0
    assert precision_at_k(retrieved, gold, k=3) == round(1 / 3, 6)
    assert hit_rate_at_k(retrieved, gold, k=1) == 1.0
    assert mrr(retrieved, gold, k=5) == 1.0
    assert ndcg_at_k(retrieved, gold, k=5) == 1.0


def test_metrics_return_none_without_gold() -> None:
    assert recall_at_k(["a"], [], k=5) is None
    assert precision_at_k(["a"], [], k=5) is None
    assert hit_rate_at_k(["a"], [], k=5) is None
    assert mrr(["a"], [], k=5) is None
    assert ndcg_at_k(["a"], [], k=5) is None


def test_miss_yields_zero_hit_and_mrr() -> None:
    assert hit_rate_at_k(["x.md"], ["y.md"], k=5) == 0.0
    assert mrr(["x.md"], ["y.md"], k=5) == 0.0


def test_variant_summary_includes_mrr() -> None:
    report = VariantReport(name="vector", label="V")
    case = CaseOutcome(
        id="1",
        question="q",
        should_answer=True,
        expected_document="a.md",
        document_hit_at_5=True,
        reciprocal_rank=1.0,
        retrieval_ms=10,
        selected_count=1,
    )
    report.cases.append(case)
    summary = report.summary()
    assert summary["recall_at_5"] == 1.0
    assert summary["mrr"] == 1.0


class _Gen:
    def __init__(self, **kwargs):
        self.should_answer = kwargs.get("should_answer", True)
        self.http_status = kwargs.get("http_status", 200)
        self.grounded = kwargs.get("grounded", False)
        self.abstained = kwargs.get("abstained", False)
        self.cited_sources = kwargs.get("cited_sources", 0)
        self.document_hit_at_5 = kwargs.get("document_hit_at_5")
        self.keyword_coverage = kwargs.get("keyword_coverage")
        self.duration_ms = kwargs.get("duration_ms", 100)
        self.embed_ms = kwargs.get("embed_ms", 10)
        self.chat_ms = kwargs.get("chat_ms", 50)
        self.load_duration_ms = kwargs.get("load_duration_ms", 0)
        self.tokens_per_second = kwargs.get("tokens_per_second", 0.0)


def test_generation_aggregates() -> None:
    results = [
        _Gen(grounded=True, cited_sources=1, document_hit_at_5=True),
        _Gen(grounded=False, cited_sources=0, document_hit_at_5=False),
        _Gen(should_answer=False, abstained=True, grounded=False),
    ]
    assert grounded_rate(results) == 0.5
    assert citation_precision(results) == 1.0
    assert abstention_correctness(results) == 1.0
    summary = summarize_generation(results)
    assert summary["measured"] is True
    assert summary["quality"]["recall_at_5"] == 0.5
