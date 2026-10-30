"""Avaliador online de evidencias (sinais deterministicos).

Nao produz score 0-100 unico. Cada criterio e explicavel e independente.
Nao confirma vigencia automaticamente — apenas observa o metadado existente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from app.services.metadata import STATUS_IN_FORCE, STATUS_UNVERIFIED
from app.services.retrieval import Candidate


@dataclass(slots=True)
class EvidenceAssessment:
    sufficient: bool
    reasons: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    selected_count: int = 0
    has_exact_reference: bool = False
    unverified_validity_count: int = 0
    conflict_suspected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sufficient": self.sufficient,
            "reasons": list(self.reasons),
            "gaps": list(self.gaps),
            "selected_count": self.selected_count,
            "has_exact_reference": self.has_exact_reference,
            "unverified_validity_count": self.unverified_validity_count,
            "conflict_suspected": self.conflict_suspected,
        }


def assess_evidence(
    selected: Sequence[Candidate],
    *,
    min_chunks: int = 1,
    require_exact_for_literal: bool = False,
    question_has_literal_ref: bool = False,
) -> EvidenceAssessment:
    assessment = EvidenceAssessment(sufficient=False, selected_count=len(selected))

    if not selected:
        assessment.gaps.append("no_chunks_selected")
        return assessment

    assessment.reasons.append(f"selected_chunks={len(selected)}")
    assessment.has_exact_reference = any(c.exact_reference for c in selected)
    if assessment.has_exact_reference:
        assessment.reasons.append("exact_reference_match")

    statuses = [
        str((c.metadata or {}).get("status") or STATUS_UNVERIFIED) for c in selected
    ]
    assessment.unverified_validity_count = sum(
        1 for status in statuses if status != STATUS_IN_FORCE
    )
    if assessment.unverified_validity_count:
        assessment.gaps.append("validity_unverified_in_selection")
        # Nao torna insuficiente so por isso — a politica e informar, nao bloquear.

    # Conflito grosseiro: dois chunks com status regulatorio distintos no top.
    distinct_statuses = {s for s in statuses if s}
    if len(distinct_statuses) > 1 and STATUS_IN_FORCE in distinct_statuses:
        assessment.conflict_suspected = True
        assessment.gaps.append("conflicting_validity_metadata")

    if len(selected) < min_chunks:
        assessment.gaps.append("below_min_chunks")
        return assessment

    if require_exact_for_literal and question_has_literal_ref:
        if not assessment.has_exact_reference:
            assessment.gaps.append("literal_ref_without_exact_match")
            return assessment

    # Suficiencia minima: ha chunks selecionados e nao ha lacuna bloqueante.
    blocking = {
        "no_chunks_selected",
        "below_min_chunks",
        "literal_ref_without_exact_match",
    }
    assessment.sufficient = not any(gap in blocking for gap in assessment.gaps)
    if assessment.sufficient:
        assessment.reasons.append("evidence_sufficient")
    return assessment
