"""Segundo estagio opcional do retrieval.

Por padrao o pipeline nao reranqueia: top-N da fusao RRF segue para
``select_context``. Quando ``RERANKER_ENABLED=true``, um reranker le de
fato o par (pergunta, trecho) e reordena.

A implementacao local reusa o Ollama (sem cross-encoder dedicado) para
caber na VRAM documentada. ``NullReranker`` e o no-op dos testes e do
default.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol, runtime_checkable

from app.services.retrieval import Candidate

logger = logging.getLogger(__name__)

_JSON_LIST = re.compile(r"\[[\s\d,\]]+\]")


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self,
        question: str,
        candidates: list[Candidate],
        *,
        limit: int,
    ) -> list[Candidate]:
        """Reordena ``candidates`` e devolve no maximo ``limit``."""


class NullReranker:
    """Identidade: preserva a ordem da fusao."""

    async def rerank(
        self,
        question: str,
        candidates: list[Candidate],
        *,
        limit: int,
    ) -> list[Candidate]:
        del question
        return list(candidates[:limit])


def parse_rank_order(raw: str, size: int) -> list[int] | None:
    """Extrai uma permutacao ``[1, 3, 2]`` (1-based) da resposta do modelo."""
    match = _JSON_LIST.search(raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    order: list[int] = []
    seen: set[int] = set()
    for item in payload:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= size and index not in seen:
            order.append(index)
            seen.add(index)
    return order or None


class OllamaPromptReranker:
    """Pede ao modelo local um ranking JSON dos trechos numerados."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def rerank(
        self,
        question: str,
        candidates: list[Candidate],
        *,
        limit: int,
    ) -> list[Candidate]:
        if len(candidates) <= 1:
            return list(candidates[:limit])

        excerpts = []
        for index, candidate in enumerate(candidates, start=1):
            excerpts.append(f"[{index}] {candidate.text[:700]}")
        prompt = (
            "Voce e um reranker de recuperacao regulatoria. "
            "Dada a pergunta e os trechos, devolva APENAS um JSON array "
            "com os numeros dos trechos em ordem de relevancia (o mais "
            "util primeiro). Nao explique.\n\n"
            f"Pergunta: {question}\n\n"
            "Trechos:\n"
            + "\n\n".join(excerpts)
        )
        try:
            result = await self._client.chat(
                "Responda somente com um array JSON de inteiros.",
                prompt,
            )
            raw = getattr(result, "content", "") or ""
        except Exception:
            logger.warning("reranker_failed", exc_info=True)
            return list(candidates[:limit])

        order = parse_rank_order(raw, len(candidates))
        if not order:
            logger.info("reranker_unparsed", extra={"preview": raw[:120]})
            return list(candidates[:limit])

        ranked = [candidates[index - 1] for index in order]
        leftover = [item for item in candidates if item not in ranked]
        return (ranked + leftover)[:limit]
