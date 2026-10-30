"""Query Planner heuristico (sem LLM).

Estende o ``query_router`` existente com categorias mais finas para o
Active Harness. Preferencias de estrategia sao configuraveis e devem ser
validadas experimentalmente via Evaluation Harness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.services.query_router import QueryPlan, route_query

_ARTICLE_REF = re.compile(
    r"\b(?:art(?:igo|\.)?\s*\d+|item\s*\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)
_LITERAL_REF = re.compile(
    r"\b(?:rdc|portaria|in|lei|decreto)\s*\d",
    re.IGNORECASE,
)
_VALIDITY = re.compile(
    r"\b(?:vigente|vigencia|revogad|alterou|alteraram|derrog)\w*\b",
    re.IGNORECASE,
)
_RELATIONSHIP = re.compile(
    r"\b(?:quais|relacion|entre normas|e\s+tamb[eé]m|alem da|al[eé]m da)\b",
    re.IGNORECASE,
)
_MULTI_QUESTION = re.compile(r"[?;].*[?;]|\be\s+(?:qual|quais|como|quando)\b", re.I)


class QueryCategory(str, Enum):
    CONCEPTUAL = "conceptual"
    EXACT_TERM = "exact_term"
    ARTICLE = "article"
    RELATIONSHIP = "relationship"
    VALIDITY = "validity"
    MULTI_QUESTION = "multi_question"


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    category: QueryCategory
    plan: QueryPlan
    preferred_strategy: str
    confidence: str  # high | medium | low


def classify_category(question: str) -> QueryCategory:
    if _MULTI_QUESTION.search(question):
        return QueryCategory.MULTI_QUESTION
    # Relacionamento antes de vigencia: "quais normas alteraram" nao e
    # consulta de status vigente/revogada.
    if _RELATIONSHIP.search(question):
        return QueryCategory.RELATIONSHIP
    if _VALIDITY.search(question):
        return QueryCategory.VALIDITY
    if _ARTICLE_REF.search(question):
        return QueryCategory.ARTICLE
    if _LITERAL_REF.search(question):
        return QueryCategory.EXACT_TERM
    return QueryCategory.CONCEPTUAL


def preferred_strategy_for(category: QueryCategory) -> str:
    mapping = {
        QueryCategory.CONCEPTUAL: "semantic",
        QueryCategory.EXACT_TERM: "bm25_plus_semantic",
        QueryCategory.ARTICLE: "bm25_exact_plus_semantic",
        QueryCategory.RELATIONSHIP: "kag_hybrid",
        QueryCategory.VALIDITY: "kag_hybrid_with_metadata",
        QueryCategory.MULTI_QUESTION: "hybrid_fallback",
    }
    return mapping[category]


def plan_query(question: str, *, kag_enabled: bool = True) -> PlannedQuery:
    category = classify_category(question)
    plan = route_query(question, kag_enabled=kag_enabled)

    # Preferencias experimentais: artigo/termo literal reforcam BM25;
    # relacionamento/vigencia reforcam grafo quando disponivel.
    if category in {QueryCategory.ARTICLE, QueryCategory.EXACT_TERM}:
        plan = QueryPlan(
            use_vector=True,
            use_bm25=True,
            use_graph=plan.use_graph,
            reason=f"{plan.reason}+{category.value}",
        )
    if category in {QueryCategory.RELATIONSHIP, QueryCategory.VALIDITY} and kag_enabled:
        plan = QueryPlan(
            use_vector=True,
            use_bm25=True,
            use_graph=True,
            reason=f"{plan.reason}+{category.value}",
        )

    confidence = "medium"
    if category in {QueryCategory.ARTICLE, QueryCategory.EXACT_TERM}:
        confidence = "high"
    elif category == QueryCategory.CONCEPTUAL:
        confidence = "low"

    return PlannedQuery(
        category=category,
        plan=plan,
        preferred_strategy=preferred_strategy_for(category),
        confidence=confidence,
    )
