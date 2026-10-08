"""Roteador heuristico da consulta.

Nao e um classificador ML. Decide, com regex barato, se a pergunta
precisa do grafo (multi-hop) ou se e uma busca normativa literal
(vetor + BM25 bastam).

O default e o caminho completo: um falso negativo (pular o grafo numa
pergunta multi-hop) custa mais do que um falso positivo (pagar o KAG
numa pergunta simples).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_LITERAL_REF = re.compile(
    r"\b(?:rdc|portaria|in|lei|decreto)\s*\d",
    re.IGNORECASE,
)
_MULTI_HOP = re.compile(
    r"\b(?:quais|relacion|revog|entre normas|e\s+tamb[eé]m)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    use_vector: bool = True
    use_bm25: bool = True
    use_graph: bool = True
    reason: str = "default"


def route_query(question: str, *, kag_enabled: bool = True) -> QueryPlan:
    """Devolve o plano de recuperacao para ``question``."""
    if not kag_enabled:
        return QueryPlan(use_graph=False, reason="kag_disabled")
    literal = bool(_LITERAL_REF.search(question))
    multi = bool(_MULTI_HOP.search(question))
    if literal and not multi:
        return QueryPlan(use_graph=False, reason="literal_ref")
    if multi:
        return QueryPlan(use_graph=True, reason="multi_hop")
    return QueryPlan(use_graph=True, reason="default")
