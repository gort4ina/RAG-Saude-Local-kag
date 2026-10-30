"""Carrega Golden Datasets em JSON (legado) ou JSONL (versionavel)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.harness.schemas import EvaluationCase

DEFAULT_JSON = Path(__file__).resolve().parents[3] / "eval" / "dataset.json"
DEFAULT_JSONL = (
    Path(__file__).resolve().parents[3] / "eval" / "datasets" / "golden_dataset.jsonl"
)


def _infer_category(raw: dict[str, Any]) -> str | None:
    if raw.get("category"):
        return str(raw["category"])
    case_id = str(raw.get("id", ""))
    if case_id.startswith("multi-"):
        return "relationship"
    if case_id.startswith("recusa-"):
        return "abstention"
    if case_id.startswith("art-") or "art." in str(raw.get("question", "")).lower():
        return "article"
    if not raw.get("should_answer", True):
        return "abstention"
    return "regulatory"


def case_from_dict(raw: dict[str, Any]) -> EvaluationCase:
    expected_doc = raw.get("expected_document")
    doc_ids = list(raw.get("expected_document_ids") or [])
    if expected_doc and not doc_ids:
        doc_ids = [str(expected_doc)]

    return EvaluationCase(
        id=str(raw.get("id", "")),
        question=str(raw["question"]),
        should_answer=bool(raw.get("should_answer", True)),
        expected_document=expected_doc or None,
        expected_document_ids=[str(item) for item in doc_ids],
        expected_evidence_ids=[
            str(item) for item in (raw.get("expected_evidence_ids") or [])
        ],
        expected_article=raw.get("expected_article") or None,
        expected_keywords=[str(w) for w in (raw.get("expected_keywords") or [])],
        reference_answer=raw.get("reference_answer") or None,
        category=_infer_category(raw),
        difficulty=raw.get("difficulty") or None,
        requires_current_validity=bool(raw.get("requires_current_validity", False)),
        review_status=str(
            raw.get("review_status") or "seeded_from_dataset_json"
        ),
    )


def load_json_dataset(path: Path) -> list[EvaluationCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        cases = payload.get("cases") or []
    elif isinstance(payload, list):
        cases = payload
    else:
        raise ValueError(f"Formato de dataset invalido em {path}")
    return [case_from_dict(item) for item in cases]


def load_jsonl_dataset(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONL invalido em {path}:{line_no}") from exc
        cases.append(case_from_dict(raw))
    return cases


def load_evaluation_cases(
    path: Path | None = None,
    *,
    prefer_jsonl: bool = False,
) -> list[EvaluationCase]:
    """Carrega casos de ``path`` ou dos defaults do repositorio.

    Preferencia: se ``path`` for passado, usa a extensao. Sem path, usa
    ``eval/dataset.json`` (canonico) salvo ``prefer_jsonl=True`` e o JSONL existir.
    """
    if path is not None:
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            return load_jsonl_dataset(path)
        return load_json_dataset(path)

    if prefer_jsonl and DEFAULT_JSONL.exists():
        return load_jsonl_dataset(DEFAULT_JSONL)
    if DEFAULT_JSON.exists():
        return load_json_dataset(DEFAULT_JSON)
    if DEFAULT_JSONL.exists():
        return load_jsonl_dataset(DEFAULT_JSONL)
    raise FileNotFoundError(
        "Nenhum dataset encontrado em eval/dataset.json nem "
        "eval/datasets/golden_dataset.jsonl"
    )


def cases_as_raw_dicts(cases: list[EvaluationCase]) -> list[dict[str, Any]]:
    """Formato legado esperado pelos CLIs / runners HTTP."""
    return [
        {
            "id": case.id,
            "question": case.question,
            "should_answer": case.should_answer,
            "expected_document": case.expected_document,
            "expected_document_ids": case.expected_document_ids,
            "expected_evidence_ids": case.expected_evidence_ids,
            "expected_article": case.expected_article,
            "expected_keywords": case.expected_keywords,
            "reference_answer": case.reference_answer,
            "category": case.category,
            "difficulty": case.difficulty,
            "requires_current_validity": case.requires_current_validity,
            "review_status": case.review_status,
        }
        for case in cases
    ]
