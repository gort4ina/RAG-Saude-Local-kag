"""Orcamento de tokens do prompt (app/services/context_budget.py)."""

from __future__ import annotations

from app.services.context_budget import (
    CHARS_PER_TOKEN,
    MIN_FIRST_BLOCK_TOKENS,
    estimate_tokens,
    fit_context,
)

SYSTEM = "s" * 2900  # ~ tamanho do prompt de sistema real
QUESTION = "O que e AFE?"


def _block(n: int, chars: int) -> str:
    return f"[Fonte {n}]\n" + ("x" * chars)


def _fit(blocks, relations=None, *, context=4096, answer=450):
    return fit_context(
        blocks,
        relations,
        system_prompt=SYSTEM,
        question=QUESTION,
        context_tokens=context,
        answer_tokens=answer,
    )


def test_estimate_tokens_is_conservative_and_handles_empty() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 33) == 10  # 33 / 3.3
    assert estimate_tokens("a") == 1


def test_everything_fits_when_chunks_are_small() -> None:
    blocks = [_block(i, 300) for i in range(1, 5)]
    kept, relations, budget = _fit(blocks, "[Relacoes]\nA revoga B")

    assert kept == blocks
    assert relations == "[Relacoes]\nA revoga B"
    assert not budget.trimmed
    assert budget.dropped_chunks == 0


def test_drops_the_least_relevant_chunks_first_and_keeps_numbering() -> None:
    # 4 chunks de ~2.200 caracteres (~670 tokens cada) estouram o orcamento
    blocks = [_block(i, 2200) for i in range(1, 5)]
    kept, _, budget = _fit(blocks, context=4096, answer=450)

    assert kept == blocks[: len(kept)]  # sempre um prefixo: [Fonte 1], [Fonte 2]...
    assert 1 <= len(kept) < 4
    assert budget.dropped_chunks == 4 - len(kept)
    assert budget.trimmed
    assert budget.estimated_prompt_tokens <= budget.context_tokens - 450


def test_relations_are_the_first_thing_to_go() -> None:
    blocks = [_block(i, 1500) for i in range(1, 4)]
    # Os 3 trechos (~1.400 tokens) cabem sozinhos nos ~2.470 disponiveis; as
    # relacoes (~1.800 tokens) fariam estourar, entao elas saem.
    relations = "[Relacoes]\n" + ("r" * 6000)
    kept_only, _, _ = _fit(blocks, None)
    assert kept_only == blocks

    kept, kept_relations, budget = _fit(blocks, relations)

    assert kept == blocks  # os trechos da evidencia continuam todos
    assert kept_relations is None
    assert budget.relations_dropped is True


def test_never_returns_empty_context_even_if_first_chunk_is_huge() -> None:
    huge = _block(1, 40_000) + ". fim."
    kept, _, budget = _fit([huge, _block(2, 500)])

    assert len(kept) == 1
    assert budget.truncated_first is True
    assert budget.dropped_chunks == 1
    assert kept[0].startswith("[Fonte 1]")
    assert kept[0].endswith("[trecho cortado para caber no contexto]")
    # cortou para caber, e nao para quase nada
    assert estimate_tokens(kept[0]) >= MIN_FIRST_BLOCK_TOKENS


def test_truncation_prefers_a_sentence_boundary() -> None:
    sentences = "Esta e uma frase completa. " * 600
    kept, _, budget = _fit(["[Fonte 1]\n" + sentences])

    assert budget.truncated_first is True
    body = kept[0].removesuffix("\n[trecho cortado para caber no contexto]")
    assert body.endswith(".")


def test_zero_or_negative_budget_still_keeps_one_truncated_chunk() -> None:
    kept, _, budget = _fit([_block(1, 5000)], context=500, answer=450)

    assert len(kept) == 1
    assert budget.available_tokens == 0
    assert budget.truncated_first is True
    assert len(kept[0]) <= int(MIN_FIRST_BLOCK_TOKENS * CHARS_PER_TOKEN) + 80
