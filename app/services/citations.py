"""Validacao de fundamentacao das respostas.

Uma resposta so e marcada como ``grounded`` quando cita pelo menos uma
fonte que realmente existe entre os trechos recuperados. Recuperar algum
documento nao basta: o modelo precisa ter usado e citado esse documento.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.metadata import STATUS_IN_FORCE

# [Fonte 1], [Fonte 1, 2], [Fontes 1 e 3], (Fonte 2)
_CITATION = re.compile(r"[\[(]\s*fontes?\s*([0-9]+(?:\s*(?:,|e|;|-)\s*[0-9]+)*)\s*[\])]", re.IGNORECASE)
_CITATION_NUMBERS = re.compile(r"\d+")

# Verbos que caracterizam uma afirmacao regulatoria (obrigacao, prazo, proibicao).
_ASSERTION = re.compile(
    r"\b(deve(?:m|ra|rao)?|e\s+obrigat[oó]ri[ao]|s[ãa]o\s+obrigat[oó]ri[ao]s|"
    r"[eé]\s+vedad[ao]|[eé]\s+proibid[ao]|exige[- ]se|fica\s+estabelecid[ao]|"
    r"o\s+prazo\s+[eé]|no\s+prazo\s+de|sob\s+pena\s+de|constitui\s+infra[çc][ãa]o)\b",
    re.IGNORECASE,
)

# Marcadores de que o modelo esta extrapolando o contexto.
_UNSUPPORTED = re.compile(
    r"\b(de\s+modo\s+geral|geralmente|normalmente|costuma[- ]se|"
    r"acredito|presumo|provavelmente|possivelmente)\b",
    re.IGNORECASE,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")

# Margem acima do threshold dentro da qual o score e considerado limitrofe.
_BORDERLINE_MARGIN = 0.10


@dataclass(slots=True)
class CitationAudit:
    """Resultado da auditoria de citacoes de uma resposta."""

    cited_indexes: list[int] = field(default_factory=list)
    invalid_indexes: list[int] = field(default_factory=list)
    grounded: bool = False
    requires_human_review: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def has_valid_citation(self) -> bool:
        return bool(self.cited_indexes)


def extract_citations(answer: str) -> list[int]:
    """Numeros citados no texto, na ordem de aparicao, sem repeticao."""
    found: list[int] = []
    for match in _CITATION.finditer(answer):
        for number in _CITATION_NUMBERS.findall(match.group(1)):
            value = int(number)
            if value not in found:
                found.append(value)
    return found


def _detect_conflict(metadatas: list[dict[str, Any]]) -> bool:
    """Duas normas distintas tratando do mesmo artigo indicam conflito."""
    by_article: dict[str, set[str]] = {}
    for metadata in metadatas:
        article = str(metadata.get("article") or "").strip().lower()
        regulation = str(metadata.get("regulation_number") or "").strip()
        if not article or not regulation:
            continue
        by_article.setdefault(article, set()).add(regulation)
    return any(len(values) > 1 for values in by_article.values())


def _split_sentences(answer: str) -> list[str]:
    """Divide em frases mantendo cada citacao junto do que ela sustenta.

    O modelo costuma escrever ``"... local. [Fonte 1]"``: a citacao vem depois
    do ponto final e viraria uma frase propria, deixando a afirmacao anterior
    aparentemente sem fonte. Uma citacao no inicio de um fragmento sempre se
    refere ao texto que a precede, entao ela e reagrupada para tras.
    """
    parts = [part.strip() for part in _SENTENCE.split(answer) if part.strip()]
    sentences: list[str] = []
    for part in parts:
        leading = _CITATION.match(part)
        if leading and sentences:
            sentences[-1] = f"{sentences[-1]} {part[: leading.end()]}"
            remainder = part[leading.end() :].strip()
            if remainder:
                sentences.append(remainder)
            continue
        sentences.append(part)
    return sentences


def _unsupported_assertions(answer: str) -> int:
    """Frases que afirmam obrigacao regulatoria sem citar fonte."""
    count = 0
    for sentence in _split_sentences(answer):
        if _CITATION.search(sentence):
            continue
        if _ASSERTION.search(sentence) or _UNSUPPORTED.search(sentence):
            count += 1
    return count


def audit_answer(
    answer: str,
    candidates: list[dict[str, Any]],
    *,
    min_relevance_score: float,
) -> CitationAudit:
    """Confere as citacoes contra os trechos realmente recuperados.

    ``candidates`` deve conter, na ordem em que foram numerados no prompt,
    dicionarios com ``score`` e ``metadata``.
    """
    audit = CitationAudit()
    total = len(candidates)

    for number in extract_citations(answer):
        if 1 <= number <= total:
            audit.cited_indexes.append(number)
        else:
            audit.invalid_indexes.append(number)

    if audit.invalid_indexes:
        audit.reasons.append(
            "A resposta citou fontes inexistentes: "
            + ", ".join(str(number) for number in audit.invalid_indexes)
        )

    if not audit.cited_indexes:
        audit.reasons.append("A resposta nao citou nenhuma fonte valida.")
        audit.grounded = False
        audit.requires_human_review = True
        return audit

    audit.grounded = True
    audit.requires_human_review = bool(audit.invalid_indexes)

    cited = [candidates[number - 1] for number in audit.cited_indexes]
    metadatas = [dict(item.get("metadata") or {}) for item in cited]

    if _detect_conflict(metadatas):
        audit.reasons.append(
            "Ha normas diferentes tratando do mesmo artigo entre as fontes citadas."
        )
        audit.requires_human_review = True

    if any(
        (metadata.get("status") or "") != STATUS_IN_FORCE for metadata in metadatas
    ):
        audit.reasons.append(
            "A vigencia de ao menos uma das fontes citadas nao esta confirmada."
        )
        audit.requires_human_review = True

    unsupported = _unsupported_assertions(answer)
    if unsupported:
        audit.reasons.append(
            f"{unsupported} afirmacao(oes) regulatoria(s) sem citacao de fonte."
        )
        audit.requires_human_review = True

    best_score = max((float(item.get("score", 0.0)) for item in cited), default=0.0)
    if best_score < min_relevance_score + _BORDERLINE_MARGIN:
        audit.reasons.append(
            "O score de similaridade das fontes citadas esta proximo do limite minimo."
        )
        audit.requires_human_review = True

    return audit
