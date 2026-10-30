"""Testes do loader de Golden Dataset."""

from __future__ import annotations

import json
from pathlib import Path

from app.harness.evaluation.dataset import (
    case_from_dict,
    load_evaluation_cases,
    load_jsonl_dataset,
)


def test_load_canonical_json_dataset() -> None:
    cases = load_evaluation_cases(Path("eval/dataset.json"))
    assert len(cases) == 41
    assert cases[0].id == "afe-001"
    assert cases[0].review_status == "seeded_from_dataset_json"
    assert cases[0].expected_document_ids == ["exemplo_afe.md"]


def test_load_golden_jsonl() -> None:
    path = Path("eval/datasets/golden_dataset.jsonl")
    cases = load_jsonl_dataset(path)
    assert len(cases) == 41
    multi = next(c for c in cases if c.id == "multi-001")
    assert multi.category == "relationship"
    refusal = next(c for c in cases if c.id == "recusa-001")
    assert refusal.should_answer is False
    assert refusal.category == "abstention"
    assert refusal.reference_answer is None


def test_case_from_dict_keeps_explicit_fields(tmp_path: Path) -> None:
    raw = {
        "id": "REG-001",
        "question": "Quais documentos tratam do controle de qualidade?",
        "expected_document_ids": ["DOC-001"],
        "expected_evidence_ids": ["CHUNK-001"],
        "category": "regulatory",
        "difficulty": "medium",
        "requires_current_validity": True,
        "review_status": "pending_human_review",
        "should_answer": True,
    }
    case = case_from_dict(raw)
    assert case.expected_document_ids == ["DOC-001"]
    assert case.expected_evidence_ids == ["CHUNK-001"]
    assert case.requires_current_validity is True
    assert case.reference_answer is None

    path = tmp_path / "one.jsonl"
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    loaded = load_jsonl_dataset(path)
    assert len(loaded) == 1
    assert loaded[0].id == "REG-001"
