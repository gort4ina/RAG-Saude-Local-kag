"""Testes do Active Harness (scaffold, sem ligar o chat)."""

from __future__ import annotations

import pytest

from app.harness.active.evidence import assess_evidence
from app.harness.active.orchestrator import ActiveHarnessOrchestrator
from app.harness.active.planner import QueryCategory, classify_category, plan_query
from app.harness.active.repair import (
    RepairState,
    decide_repair,
    merge_document_lists,
    rewrite_query,
)
from app.harness.settings import HarnessSettings
from app.services.metadata import STATUS_IN_FORCE, STATUS_UNVERIFIED
from app.services.retrieval import Candidate


def test_planner_categories() -> None:
    assert classify_category("O que e controle de qualidade?") == QueryCategory.CONCEPTUAL
    assert classify_category("O que determina o item 9.2 da RDC 67/2007?") == QueryCategory.ARTICLE
    assert (
        classify_category("Quais normas alteraram determinada exigencia?")
        == QueryCategory.RELATIONSHIP
    )
    assert classify_category("A RDC 67/2007 ainda esta vigente?") == QueryCategory.VALIDITY


def test_plan_query_uses_router_without_llm() -> None:
    planned = plan_query("O que diz a RDC 67/2007?", kag_enabled=True)
    assert planned.category == QueryCategory.EXACT_TERM
    assert planned.plan.use_bm25 is True
    assert planned.preferred_strategy == "bm25_plus_semantic"


def test_rewrite_expands_refs_and_drops_stopwords() -> None:
    expanded = rewrite_query("o que diz a portaria 344/98?", strategy="expand_refs")
    assert "344" in expanded
    dropped = rewrite_query("o que diz sobre manipulacao", strategy="drop_stopwords")
    assert "o" not in dropped.split()
    assert "manipulacao" in dropped


def test_decide_repair_respects_max_retries() -> None:
    state = RepairState(attempts=2, previous_queries=["q"])
    decision = decide_repair(
        state=state,
        max_retries=2,
        evidence_sufficient=False,
        gaps=["no_chunks_selected"],
        original_query="q",
    )
    assert decision.should_retry is False
    assert decision.exhausted is True
    assert decision.reason == "max_retries_reached"


def test_decide_repair_rewrites_when_insufficient() -> None:
    state = RepairState(previous_queries=["o que diz a rdc 67/2007?"])
    decision = decide_repair(
        state=state,
        max_retries=2,
        evidence_sufficient=False,
        gaps=["no_chunks_selected"],
        original_query="o que diz a rdc 67/2007?",
    )
    assert decision.should_retry is True
    assert decision.rewritten_query
    assert decision.attempt == 1


def test_merge_preserves_previous_hits() -> None:
    merged = merge_document_lists(["a.md", "b.md"], ["b.md", "c.md"], limit=10)
    assert merged == ["a.md", "b.md", "c.md"]


def test_assess_evidence_empty_is_insufficient() -> None:
    result = assess_evidence([])
    assert result.sufficient is False
    assert "no_chunks_selected" in result.gaps


def test_assess_evidence_flags_unverified_without_blocking() -> None:
    candidate = Candidate(
        chunk_id="c1",
        text="texto",
        metadata={"status": STATUS_UNVERIFIED},
        vector_score=0.9,
    )
    result = assess_evidence([candidate])
    assert result.sufficient is True
    assert "validity_unverified_in_selection" in result.gaps
    assert result.unverified_validity_count == 1


def test_assess_evidence_conflict_suspected() -> None:
    left = Candidate(
        chunk_id="c1",
        text="a",
        metadata={"status": STATUS_IN_FORCE},
        vector_score=0.9,
    )
    right = Candidate(
        chunk_id="c2",
        text="b",
        metadata={"status": STATUS_UNVERIFIED},
        vector_score=0.8,
    )
    result = assess_evidence([left, right])
    assert result.conflict_suspected is True


def test_harness_settings_default_disables_active_runtime(monkeypatch) -> None:
    monkeypatch.delenv("HARNESS_ENABLED", raising=False)
    monkeypatch.delenv("HARNESS_MODE", raising=False)
    settings = HarnessSettings()
    assert settings.enabled is False
    assert settings.mode == "evaluation"
    assert settings.active_runtime_enabled is False


@pytest.mark.asyncio
async def test_orchestrator_stops_when_evidence_ok() -> None:
    settings = HarnessSettings(
        enabled=True,
        mode="active",
        max_retries=2,
        query_rewrite_enabled=True,
        evidence_validation_enabled=True,
        citation_validation_enabled=True,
        trace_enabled=True,
    )
    orchestrator = ActiveHarnessOrchestrator(settings)

    class Outcome:
        selected = [
            Candidate(
                chunk_id="c1",
                text="Art. 5",
                metadata={"status": STATUS_IN_FORCE},
                vector_score=0.9,
                exact_reference=True,
            )
        ]
        sources = [type("S", (), {"document": "exemplo.md"})()]

    calls = {"n": 0}

    async def retrieve(query: str):
        calls["n"] += 1
        return Outcome()

    result = await orchestrator.run("O que diz o art. 5?", retrieve=retrieve)
    assert calls["n"] == 1
    assert result.assessment.sufficient is True
    assert result.retries == 0
    assert result.trace.query_category is not None
    assert result.trace.query_hash is not None


@pytest.mark.asyncio
async def test_orchestrator_retries_then_stops() -> None:
    settings = HarnessSettings(
        enabled=True,
        mode="active",
        max_retries=1,
        query_rewrite_enabled=True,
        evidence_validation_enabled=True,
        citation_validation_enabled=True,
        trace_enabled=True,
    )
    orchestrator = ActiveHarnessOrchestrator(settings)

    class Empty:
        selected = []
        sources = []

    calls = {"n": 0}

    async def retrieve(query: str):
        calls["n"] += 1
        return Empty()

    result = await orchestrator.run("pergunta sem evidencia", retrieve=retrieve)
    assert calls["n"] == 2  # initial + 1 retry
    assert result.assessment.sufficient is False
    assert result.repair.exhausted or result.retries >= 1
