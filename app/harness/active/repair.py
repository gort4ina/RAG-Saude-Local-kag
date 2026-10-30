"""Repair Loop — reformulacao controlada e limites de retry.

Nao aceita automaticamente a ultima busca como superior: a decisao de
manter/mesclar fica no orquestrador via comparacao de conjuntos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_STOPWORDS = {
    "a",
    "o",
    "os",
    "as",
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "em",
    "um",
    "uma",
    "para",
    "por",
    "que",
    "qual",
    "quais",
    "como",
    "sobre",
    "me",
    "diga",
    "explique",
}

_REF_EXPAND = re.compile(
    r"\b(rdc|portaria|in|lei|decreto)\s*(\d+)(?:/(\d{2,4}))?\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RepairDecision:
    should_retry: bool
    reason: str
    rewritten_query: str | None = None
    attempt: int = 0
    exhausted: bool = False


@dataclass(slots=True)
class RepairState:
    attempts: int = 0
    previous_queries: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def rewrite_query(question: str, *, strategy: str = "expand_refs") -> str:
    """Reformula a consulta de forma deterministica.

    Estrategias:
    - ``expand_refs``: duplica variantes de RDC/Portaria (ano curto/longo).
    - ``drop_stopwords``: remove palavras funcionais comuns.
    """
    if strategy == "drop_stopwords":
        tokens = [
            token
            for token in re.findall(r"\w+", question.casefold(), flags=re.UNICODE)
            if token not in _STOPWORDS and len(token) > 1
        ]
        return " ".join(tokens) or question

    # expand_refs (default)
    parts = [question]

    def _expand(match: re.Match[str]) -> str:
        kind, number, year = match.group(1), match.group(2), match.group(3)
        base = f"{kind.upper()} {number}"
        extras: list[str] = []
        if year:
            extras.append(f"{base}/{year}")
            if len(year) == 2:
                extras.append(f"{base}/19{year}" if int(year) >= 50 else f"{base}/20{year}")
            elif len(year) == 4:
                extras.append(f"{base}/{year[2:]}")
        else:
            extras.append(base)
        return " ".join(dict.fromkeys(extras))

    expanded = _REF_EXPAND.sub(_expand, question)
    if expanded != question:
        parts.append(expanded)
    # Une termos unicos preservando ordem.
    merged = " ".join(dict.fromkeys(" ".join(parts).split()))
    return merged


def decide_repair(
    *,
    state: RepairState,
    max_retries: int,
    evidence_sufficient: bool,
    gaps: list[str],
    original_query: str,
    rewrite_enabled: bool = True,
) -> RepairDecision:
    if evidence_sufficient:
        return RepairDecision(
            should_retry=False,
            reason="evidence_sufficient",
            attempt=state.attempts,
        )
    if state.attempts >= max_retries:
        return RepairDecision(
            should_retry=False,
            reason="max_retries_reached",
            attempt=state.attempts,
            exhausted=True,
        )

    if not rewrite_enabled:
        return RepairDecision(
            should_retry=False,
            reason="rewrite_disabled",
            attempt=state.attempts,
            exhausted=True,
        )

    strategy = "expand_refs" if state.attempts == 0 else "drop_stopwords"
    rewritten = rewrite_query(original_query, strategy=strategy)
    if rewritten in state.previous_queries:
        alt_strategy = "drop_stopwords" if strategy == "expand_refs" else "expand_refs"
        rewritten = rewrite_query(original_query, strategy=alt_strategy)
    if rewritten in state.previous_queries:
        # Ultimo recurso deterministico: reforco lexical, sem LLM.
        rewritten = f"{original_query} regulamentacao documento normativo"
    if rewritten in state.previous_queries:
        return RepairDecision(
            should_retry=False,
            reason="rewrite_exhausted",
            attempt=state.attempts,
            exhausted=True,
        )

    reason = f"insufficient_evidence:{','.join(gaps) or 'unknown'}"
    return RepairDecision(
        should_retry=True,
        reason=reason,
        rewritten_query=rewritten,
        attempt=state.attempts + 1,
    )


def merge_document_lists(
    primary: list[str], secondary: list[str], *, limit: int = 12
) -> list[str]:
    """Preserva resultados validos anteriores e acrescenta novos sem duplicar."""
    merged: list[str] = []
    seen: set[str] = set()
    for item in primary + secondary:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged
