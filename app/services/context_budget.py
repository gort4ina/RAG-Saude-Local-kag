"""Orcamento de tokens do prompt.

O contexto do modelo (``OLLAMA_CONTEXT_LENGTH``, 4096 por padrao) precisa
caber: prompt de sistema + trechos recuperados + relacoes do grafo + pergunta
+ a resposta que ainda sera gerada (``OLLAMA_NUM_PREDICT``). Quando passa
disso o Ollama trunca o prompt por conta propria e so avisa no log do servidor,
ou seja, o modelo responde sem ter visto parte da evidencia e ninguem sabe.

Aqui a decisao e nossa e e visivel: o que nao cabe e removido por inteiro, do
trecho menos relevante para o mais relevante, e isso e registrado.

A contagem e uma ESTIMATIVA por caracteres (nao ha tokenizador no pipeline).
``CHARS_PER_TOKEN`` e conservador de proposito: portugues tokeniza em ~3,5 a
4 caracteres por token nos modelos Qwen/Llama, e errar para menos so faz
deixarmos um pouco de folga. O valor real (``prompt_eval_count``) volta do
Ollama a cada resposta e e logado ao lado da estimativa para calibrar isto.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CHARS_PER_TOKEN = 3.3
# Delimitadores do contexto, linha da pergunta e marcadores do template de chat.
PROMPT_OVERHEAD_TOKENS = 80
# Folga sobre o contexto total: a estimativa pode errar um pouco.
SAFETY_MARGIN = 0.05
# Se nem o primeiro trecho cabe, ele e cortado, mas nunca abaixo disto.
MIN_FIRST_BLOCK_TOKENS = 256

_TRUNCATION_NOTE = "\n[trecho cortado para caber no contexto]"


def estimate_tokens(text: str) -> int:
    """Estimativa conservadora de tokens de ``text``."""
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """O que o orcamento decidiu, para log e metricas."""

    context_tokens: int
    available_tokens: int
    estimated_prompt_tokens: int
    kept_chunks: int
    dropped_chunks: int = 0
    truncated_first: bool = False
    relations_dropped: bool = False

    @property
    def trimmed(self) -> bool:
        return bool(
            self.dropped_chunks or self.truncated_first or self.relations_dropped
        )


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Corta ``text`` em ``max_chars``, de preferencia no fim de uma frase."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    boundary = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("\n"))
    if boundary >= max_chars * 0.5:
        cut = cut[: boundary + 1]
    return cut.rstrip() + _TRUNCATION_NOTE


def fit_context(
    blocks: list[str],
    relations_block: str | None,
    *,
    system_prompt: str,
    question: str,
    context_tokens: int,
    answer_tokens: int,
) -> tuple[list[str], str | None, ContextBudget]:
    """Escolhe quais blocos cabem no contexto do modelo.

    ``blocks`` vem em ordem de relevancia (``[Fonte 1]`` e o melhor). Mantem o
    maior prefixo que cabe, de modo que a numeracao das fontes continua valida.
    As relacoes do grafo sao so orientacao, entao entram por ultimo e saem
    primeiro. Nunca devolve contexto vazio: se nem o primeiro bloco cabe, ele e
    cortado em fim de frase.

    Devolve ``(blocos_mantidos, relacoes_ou_None, decisao)``.
    """
    fixed = (
        estimate_tokens(system_prompt)
        + estimate_tokens(question)
        + PROMPT_OVERHEAD_TOKENS
    )
    available = max(
        int(context_tokens * (1 - SAFETY_MARGIN)) - answer_tokens - fixed, 0
    )

    kept: list[str] = []
    used = 0
    truncated_first = False
    for index, block in enumerate(blocks):
        cost = estimate_tokens(block) + 1  # +1: separador entre blocos
        if used + cost <= available:
            kept.append(block)
            used += cost
            continue
        if index == 0:
            allowed = max(available, MIN_FIRST_BLOCK_TOKENS)
            truncated = _truncate_at_sentence(block, int(allowed * CHARS_PER_TOKEN))
            kept.append(truncated)
            used += estimate_tokens(truncated) + 1
            truncated_first = True
        break

    relations = relations_block
    relations_dropped = False
    if relations:
        cost = estimate_tokens(relations) + 1
        if used + cost <= available:
            used += cost
        else:
            relations = None
            relations_dropped = True

    budget = ContextBudget(
        context_tokens=context_tokens,
        available_tokens=available,
        estimated_prompt_tokens=fixed + used,
        kept_chunks=len(kept),
        dropped_chunks=len(blocks) - len(kept),
        truncated_first=truncated_first,
        relations_dropped=relations_dropped,
    )
    return kept, relations, budget
