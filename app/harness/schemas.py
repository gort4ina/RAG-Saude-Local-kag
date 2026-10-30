"""Schemas compartilhados do AI Harness."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class EvaluationCase:
    """Um caso do Golden Dataset (offline).

    ``reference_answer`` permanece opcional e so deve ser preenchido apos
    revisao humana — nunca inventado pelo harness.
    """

    id: str
    question: str
    should_answer: bool = True
    expected_document: str | None = None
    expected_document_ids: list[str] = field(default_factory=list)
    expected_evidence_ids: list[str] = field(default_factory=list)
    expected_article: str | None = None
    expected_keywords: list[str] = field(default_factory=list)
    reference_answer: str | None = None
    category: str | None = None
    difficulty: str | None = None
    requires_current_validity: bool = False
    review_status: str = "pending_human_review"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CaseOutcome:
    """Resultado de retrieval-only para um caso (ablacao / runner offline)."""

    id: str
    question: str
    should_answer: bool
    expected_document: str | None
    retrieved_documents: list[str] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    document_hit_at_5: bool | None = None
    reciprocal_rank: float | None = None
    retrieval_ms: int = 0
    embed_ms: int = 0
    vector_query_ms: int = 0
    bm25_query_ms: int = 0
    graph_query_ms: int = 0
    fusion_ms: int = 0
    fused_count: int = 0
    selected_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VariantReport:
    """Relatorio de uma variante de retrieval (V / V+B / V+B+K)."""

    name: str
    label: str
    cases: list[CaseOutcome] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        from app.harness.metrics.retrieval import summarize_variant

        return summarize_variant(self)


@dataclass(slots=True)
class HarnessTrace:
    """Trilha rastreavel de uma execucao (evaluation report ou active)."""

    trace_id: str
    request_id: str | None = None
    query_category: str | None = None
    retrieval_strategy: str | None = None
    retrieved_document_ids: list[str] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    retrieval_scores: list[float] = field(default_factory=list)
    reranking_scores: list[float] = field(default_factory=list)
    retry_count: int = 0
    retry_reasons: list[str] = field(default_factory=list)
    selected_evidence_ids: list[str] = field(default_factory=list)
    document_versions: list[str] = field(default_factory=list)
    model_name: str | None = None
    retrieval_duration_ms: int | None = None
    generation_duration_ms: int | None = None
    validation_status: str | None = None
    error_type: str | None = None
    # Nunca gravar a pergunta literal aqui por default (LGPD).
    query_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
