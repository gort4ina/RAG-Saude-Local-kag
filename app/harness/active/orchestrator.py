"""Orquestrador do Active Harness.

Importavel e testavel, mas so deve ser chamado pelo caminho de chat quando
``HarnessSettings.active_runtime_enabled`` for True. Este modulo NAO e
ligado automaticamente em ``RagService`` neste PR.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.harness.active.evidence import EvidenceAssessment, assess_evidence
from app.harness.active.planner import PlannedQuery, plan_query
from app.harness.active.repair import RepairDecision, RepairState, decide_repair
from app.harness.schemas import HarnessTrace
from app.harness.settings import HarnessSettings, get_harness_settings
from app.services.retrieval import Candidate

RetrieveFn = Callable[[str], Awaitable[Any]]


@dataclass(slots=True)
class OrchestratorResult:
    query_used: str
    planned: PlannedQuery
    assessment: EvidenceAssessment
    repair: RepairDecision
    trace: HarnessTrace
    selected: list[Candidate] = field(default_factory=list)
    retrieved_documents: list[str] = field(default_factory=list)
    retries: int = 0


class ActiveHarnessOrchestrator:
    """Coordena planner → retrieve → evidence → repair (com limites)."""

    def __init__(self, settings: HarnessSettings | None = None) -> None:
        self.settings = settings or get_harness_settings()

    async def run(
        self,
        question: str,
        *,
        retrieve: RetrieveFn,
        request_id: str | None = None,
        kag_enabled: bool = True,
        extract_selected: Callable[[Any], list[Candidate]] | None = None,
        extract_documents: Callable[[Any], list[str]] | None = None,
    ) -> OrchestratorResult:
        planned = plan_query(question, kag_enabled=kag_enabled)
        state = RepairState(previous_queries=[question])
        current_query = question
        selected: list[Candidate] = []
        documents: list[str] = []
        assessment = EvidenceAssessment(sufficient=False, gaps=["not_run"])
        repair = RepairDecision(should_retry=False, reason="init")

        get_selected = extract_selected or (
            lambda outcome: list(getattr(outcome, "selected", []) or [])
        )
        get_docs = extract_documents or (
            lambda outcome: [
                str(getattr(src, "document", ""))
                for src in (getattr(outcome, "sources", []) or [])
            ]
        )

        while True:
            outcome = await retrieve(current_query)
            selected = get_selected(outcome)
            documents = get_docs(outcome)
            assessment = assess_evidence(
                selected,
                question_has_literal_ref=planned.category.value
                in {"article", "exact_term"},
                require_exact_for_literal=False,
            )
            repair = decide_repair(
                state=state,
                max_retries=self.settings.max_retries,
                evidence_sufficient=assessment.sufficient,
                gaps=assessment.gaps,
                original_query=question,
                rewrite_enabled=self.settings.query_rewrite_enabled,
            )
            if not repair.should_retry or not self.settings.evidence_validation_enabled:
                break
            assert repair.rewritten_query is not None
            state.attempts = repair.attempt
            state.reasons.append(repair.reason)
            state.previous_queries.append(repair.rewritten_query)
            current_query = repair.rewritten_query

        trace = HarnessTrace(
            trace_id=str(uuid.uuid4()),
            request_id=request_id,
            query_category=planned.category.value,
            retrieval_strategy=planned.preferred_strategy,
            retrieved_document_ids=documents,
            retrieved_chunk_ids=[c.chunk_id for c in selected],
            retrieval_scores=[c.score for c in selected],
            retry_count=state.attempts,
            retry_reasons=list(state.reasons),
            selected_evidence_ids=[c.chunk_id for c in selected],
            validation_status=(
                "sufficient" if assessment.sufficient else "insufficient"
            ),
            query_hash=hashlib.sha256(question.encode("utf-8")).hexdigest()[:16],
        )

        return OrchestratorResult(
            query_used=current_query,
            planned=planned,
            assessment=assessment,
            repair=repair,
            trace=trace,
            selected=selected,
            retrieved_documents=documents,
            retries=state.attempts,
        )
