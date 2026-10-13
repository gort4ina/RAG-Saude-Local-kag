"""Fusao de resultados densos e lexicais, deduplicacao e selecao final.

Estrategia:

1. busca vetorial (ChromaDB) devolve N candidatos;
2. busca BM25 devolve N candidatos;
3. as duas listas sao combinadas por Reciprocal Rank Fusion (RRF);
4. chunks quase identicos sao deduplicados por similaridade de tokens;
5. chunks adjacentes do mesmo documento com muita sobreposicao sao reduzidos
   a um;
6. sobram no maximo ``max_context_chunks`` para o prompt.

Sobre o score reportado:

- ``score`` e a similaridade de cosseno devolvida pelo ChromaDB (0..1).
  Um candidato que so apareceu no BM25 nao tem cosseno e recebe 0.0;
- ``lexical_score`` e o BM25 normalizado pelo maior BM25 da propria
  consulta, portanto so serve para comparar candidatos entre si;
- um candidato passa no filtro de relevancia se o cosseno atingir o
  threshold OU se ele contiver literalmente a referencia normativa citada
  na pergunta (ex.: "RDC 67/2007", "art. 5") com BM25 relativo alto.
  Esse segundo caminho existe porque perguntas por numero de norma tem
  cosseno baixo e resposta obvia.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.bm25_index import Bm25Hit, reference_tokens, tokenize

logger = logging.getLogger(__name__)

# BM25 relativo minimo para que uma referencia normativa literal seja aceita
# mesmo com cosseno abaixo do threshold.
_LEXICAL_OVERRIDE_MIN = 0.5

# Sobreposicao de tokens acima da qual dois chunks adjacentes do mesmo
# documento sao considerados repetidos.
_ADJACENT_OVERLAP = 0.5


@dataclass(slots=True)
class Candidate:
    """Um chunk candidato apos a fusao."""

    chunk_id: str
    text: str
    metadata: dict[str, Any]
    vector_score: float = 0.0
    vector_rank: int | None = None
    bm25_score: float = 0.0
    bm25_rank: int | None = None
    lexical_score: float = 0.0
    exact_reference: bool = False
    rrf_score: float = 0.0
    tokens: frozenset[str] = field(default_factory=frozenset)

    @property
    def score(self) -> float:
        return self.vector_score

    @property
    def document_id(self) -> str:
        return str(self.metadata.get("document_id", ""))

    @property
    def chunk_number(self) -> int:
        try:
            return int(self.metadata.get("chunk", 0))
        except (TypeError, ValueError):
            return 0


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def fuse(
    vector_results: list[dict[str, Any]],
    bm25_hits: list[Bm25Hit],
    lexical_documents: dict[str, dict[str, Any]],
    *,
    question: str,
    rrf_k: int = 60,
    graph_hits: list[dict[str, Any]] | None = None,
) -> list[Candidate]:
    """Combina vetor, BM25 e (opcionalmente) chunks puxados pelo grafo.

    ``lexical_documents`` traz texto e metadados dos chunks que apareceram
    apenas no BM25 (o ChromaDB nao os devolveu). ``graph_hits`` tem o
    mesmo formato dos resultados vetoriais e entra como terceira lista
    do RRF — o KAG deixa de ser so "orientacao" no prompt.
    """
    candidates: dict[str, Candidate] = {}

    for rank, item in enumerate(vector_results, start=1):
        chunk_id = str(item.get("id") or f"vector:{rank}")
        candidates[chunk_id] = Candidate(
            chunk_id=chunk_id,
            text=str(item.get("text", "")),
            metadata=dict(item.get("metadata") or {}),
            vector_score=float(item.get("score", 0.0)),
            vector_rank=rank,
            rrf_score=1.0 / (rrf_k + rank),
        )

    max_bm25 = max((hit.score for hit in bm25_hits), default=0.0)
    for rank, hit in enumerate(bm25_hits, start=1):
        normalized = (hit.score / max_bm25) if max_bm25 > 0 else 0.0
        existing = candidates.get(hit.chunk_id)
        if existing is not None:
            existing.bm25_score = hit.score
            existing.bm25_rank = rank
            existing.lexical_score = normalized
            existing.rrf_score += 1.0 / (rrf_k + rank)
            continue

        source = lexical_documents.get(hit.chunk_id)
        if source is None:
            continue
        candidates[hit.chunk_id] = Candidate(
            chunk_id=hit.chunk_id,
            text=str(source.get("text", "")),
            metadata=dict(source.get("metadata") or {}),
            bm25_score=hit.score,
            bm25_rank=rank,
            lexical_score=normalized,
            rrf_score=1.0 / (rrf_k + rank),
        )

    for rank, item in enumerate(graph_hits or [], start=1):
        chunk_id = str(item.get("id") or f"graph:{rank}")
        existing = candidates.get(chunk_id)
        if existing is not None:
            existing.rrf_score += 1.0 / (rrf_k + rank)
            continue
        candidates[chunk_id] = Candidate(
            chunk_id=chunk_id,
            text=str(item.get("text", "")),
            metadata=dict(item.get("metadata") or {}),
            vector_score=float(item.get("score", 0.0)),
            rrf_score=1.0 / (rrf_k + rank),
        )

    question_references = reference_tokens(question)
    for candidate in candidates.values():
        candidate.tokens = frozenset(tokenize(candidate.text))
        candidate.exact_reference = bool(
            question_references and question_references & candidate.tokens
        )

    return sorted(candidates.values(), key=lambda item: item.rrf_score, reverse=True)


def passes_relevance(candidate: Candidate, min_relevance_score: float) -> bool:
    """Aplica o threshold denso com escape para referencia normativa literal."""
    if candidate.vector_score >= min_relevance_score:
        return True
    return candidate.exact_reference and candidate.lexical_score >= _LEXICAL_OVERRIDE_MIN


def deduplicate(
    candidates: list[Candidate], *, similarity_threshold: float = 0.90
) -> list[Candidate]:
    """Remove quase-duplicatas e reduz chunks adjacentes repetidos."""
    selected: list[Candidate] = []
    for candidate in candidates:
        duplicate = False
        for kept in selected:
            overlap = _jaccard(candidate.tokens, kept.tokens)
            if overlap >= similarity_threshold:
                duplicate = True
                break
            adjacent = (
                candidate.document_id
                and candidate.document_id == kept.document_id
                and abs(candidate.chunk_number - kept.chunk_number) <= 1
            )
            if adjacent and overlap >= _ADJACENT_OVERLAP:
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
    return selected


def select_context(
    candidates: list[Candidate],
    *,
    min_relevance_score: float,
    max_context_chunks: int,
    dedup_similarity: float = 0.90,
) -> list[Candidate]:
    """Filtra por relevancia, deduplica e corta no limite do prompt."""
    relevant = [
        candidate
        for candidate in candidates
        if passes_relevance(candidate, min_relevance_score)
    ]
    return deduplicate(relevant, similarity_threshold=dedup_similarity)[
        :max_context_chunks
    ]
