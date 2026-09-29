"""Testes da validacao de fundamentacao."""

from __future__ import annotations

from app.services.citations import audit_answer, extract_citations
from app.services.metadata import STATUS_IN_FORCE, STATUS_UNVERIFIED


def _candidates(count: int, *, score: float = 0.90, status: str = STATUS_IN_FORCE):
    return [
        {
            "score": score,
            "metadata": {
                "filename": f"doc{index}.pdf",
                "status": status,
                "regulation_number": f"RDC {index}/2020",
                "article": f"Art. {index}",
            },
        }
        for index in range(1, count + 1)
    ]


def test_extract_citations_handles_multiple_formats() -> None:
    assert extract_citations("Conforme [Fonte 1] e [Fonte 2].") == [1, 2]
    assert extract_citations("Ver [Fontes 1, 3].") == [1, 3]
    assert extract_citations("Ver (Fonte 4).") == [4]
    assert extract_citations("Sem citacao alguma.") == []


def test_extract_citations_deduplicates() -> None:
    assert extract_citations("[Fonte 1] ... [Fonte 1] ... [Fonte 2]") == [1, 2]


def test_valid_citation_marks_answer_as_grounded() -> None:
    audit = audit_answer(
        "O registro e exigido [Fonte 1].",
        _candidates(2),
        min_relevance_score=0.45,
    )
    assert audit.grounded is True
    assert audit.cited_indexes == [1]
    assert audit.invalid_indexes == []


def test_answer_without_citation_is_not_grounded() -> None:
    audit = audit_answer(
        "O estabelecimento deve manter registros atualizados.",
        _candidates(2),
        min_relevance_score=0.45,
    )
    assert audit.grounded is False
    assert audit.requires_human_review is True
    assert any("nao citou" in reason for reason in audit.reasons)


def test_nonexistent_citation_is_rejected() -> None:
    audit = audit_answer(
        "Conforme [Fonte 7], o prazo e de 30 dias.",
        _candidates(2),
        min_relevance_score=0.45,
    )
    assert audit.cited_indexes == []
    assert audit.invalid_indexes == [7]
    assert audit.grounded is False


def test_mixed_valid_and_invalid_citations_require_review() -> None:
    audit = audit_answer(
        "Conforme [Fonte 1] e [Fonte 9].",
        _candidates(2),
        min_relevance_score=0.45,
    )
    assert audit.cited_indexes == [1]
    assert audit.invalid_indexes == [9]
    assert audit.grounded is True
    assert audit.requires_human_review is True


def test_only_cited_sources_are_returned() -> None:
    audit = audit_answer(
        "Somente a [Fonte 3] sustenta isso.",
        _candidates(4),
        min_relevance_score=0.45,
    )
    assert audit.cited_indexes == [3]


def test_unverified_status_requires_review() -> None:
    audit = audit_answer(
        "Exigencia confirmada [Fonte 1].",
        _candidates(1, status=STATUS_UNVERIFIED),
        min_relevance_score=0.45,
    )
    assert audit.grounded is True
    assert audit.requires_human_review is True
    assert any("vigencia" in reason.lower() for reason in audit.reasons)


def test_borderline_score_requires_review() -> None:
    audit = audit_answer(
        "Exigencia confirmada [Fonte 1].",
        _candidates(1, score=0.47),
        min_relevance_score=0.45,
    )
    assert audit.requires_human_review is True
    assert any("limite" in reason.lower() for reason in audit.reasons)


def test_document_conflict_requires_review() -> None:
    candidates = [
        {
            "score": 0.9,
            "metadata": {
                "status": STATUS_IN_FORCE,
                "article": "Art. 5",
                "regulation_number": "RDC 67/2007",
            },
        },
        {
            "score": 0.9,
            "metadata": {
                "status": STATUS_IN_FORCE,
                "article": "Art. 5",
                "regulation_number": "RDC 87/2008",
            },
        },
    ]
    audit = audit_answer(
        "As duas normas divergem [Fonte 1] [Fonte 2].",
        candidates,
        min_relevance_score=0.45,
    )
    assert audit.requires_human_review is True
    assert any("mesmo artigo" in reason for reason in audit.reasons)


def test_uncited_regulatory_assertion_requires_review() -> None:
    audit = audit_answer(
        "O registro e exigido [Fonte 1]. Alem disso, a farmacia deve manter "
        "um livro de ocorrencias.",
        _candidates(1),
        min_relevance_score=0.45,
    )
    assert audit.grounded is True
    assert audit.requires_human_review is True
    assert any("sem citacao" in reason for reason in audit.reasons)


def test_citation_after_the_final_period_still_supports_the_sentence() -> None:
    """Resposta real do qwen2.5:3b: a citacao vem depois do ponto final."""
    audit = audit_answer(
        "Os registros de manipulacao devem ser conservados por, no minimo, "
        "6 meses apos a data de validade da preparacao, salvo exigencia mais "
        "restritiva da autoridade sanitaria local. [Fonte 1]",
        _candidates(1),
        min_relevance_score=0.45,
    )
    assert audit.grounded is True
    assert audit.requires_human_review is False
    assert audit.reasons == []


def test_citation_opening_a_sentence_supports_the_previous_one() -> None:
    audit = audit_answer(
        "A farmacia deve qualificar o fornecedor. [Fonte 1] O certificado de "
        "analise acompanha cada lote. [Fonte 1]",
        _candidates(1),
        min_relevance_score=0.45,
    )
    assert audit.requires_human_review is False


def test_text_after_a_leading_citation_still_needs_its_own_source() -> None:
    audit = audit_answer(
        "A farmacia deve qualificar o fornecedor. [Fonte 1] Tambem deve manter "
        "um livro de ocorrencias.",
        _candidates(1),
        min_relevance_score=0.45,
    )
    assert audit.requires_human_review is True
    assert any("sem citacao" in reason for reason in audit.reasons)
