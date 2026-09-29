"""Indice BM25 em memoria, sem dependencias externas.

Motivacao: busca densa pura erra identificadores regulatorios. "RDC 67/2007",
"Portaria 344/98", "AFE", "SNGPC" e "art. 5" sao tokens raros e literais -
exatamente o cenario em que BM25 ganha do embedding.

O indice e reconstruido na inicializacao e apos cada ingestao, nunca a cada
pergunta. Consultar apenas percorre a lista invertida.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_K1 = 1.5
_B = 0.75

_WORD = re.compile(r"[a-z0-9]+")
# 67/2007, 344/98, 5.991/1973
_NUMBER_YEAR = re.compile(r"(\d{1,6}(?:\.\d{3})*)\s*[/-]\s*(\d{2,4})")
# art. 5, artigo 5o, art 5-A
_ARTICLE_REF = re.compile(r"\bart(?:igo)?\.?\s*(\d{1,4})(?:\s*-\s*([a-z]))?")

# Stopwords portuguesas de altissima frequencia. Lista curta de proposito:
# remover demais prejudica frases regulatorias curtas.
_STOPWORDS = frozenset(
    """
    a o e de da do das dos em no na nos nas um uma uns umas para por com sem
    que se ao aos as os ou como mais menos ser sao foi seu sua seus suas
    este esta estes estas esse essa isso aquele aquela pelo pela pelos pelas
    entre sobre ate apos antes quando onde qual quais cujo cuja
    """.split()
)


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _expand_year(year: str) -> str | None:
    """``98`` -> ``1998``; ``07`` -> ``2007``."""
    if len(year) != 2 or not year.isdigit():
        return None
    value = int(year)
    return f"19{year}" if value > 50 else f"20{year}"


def tokenize(text: str) -> list[str]:
    """Tokeniza preservando identificadores normativos.

    Alem das palavras, emite tokens compostos para que "Portaria 344/98"
    e "Portaria 344, de 1998" caiam no mesmo termo:

    - ``344/98`` -> ``344/98``, ``344/1998``, ``344``, ``98``, ``1998``;
    - ``art. 5`` -> ``art5``.
    """
    lowered = strip_accents(text.lower())
    tokens = [token for token in _WORD.findall(lowered) if token not in _STOPWORDS]

    for match in _NUMBER_YEAR.finditer(lowered):
        number = match.group(1).replace(".", "")
        year = match.group(2)
        tokens.append(f"{number}/{year}")
        if expanded := _expand_year(year):
            tokens.append(f"{number}/{expanded}")
            tokens.append(expanded)
        elif len(year) == 4:
            tokens.append(f"{number}/{year[2:]}")

    for match in _ARTICLE_REF.finditer(lowered):
        suffix = match.group(2) or ""
        tokens.append(f"art{match.group(1)}{suffix}")

    return tokens


def reference_tokens(text: str) -> set[str]:
    """Tokens que identificam uma norma ou artigo de forma literal.

    Usados para detectar quando um chunk contem exatamente a referencia
    citada na pergunta, mesmo que o score denso seja baixo.
    """
    lowered = strip_accents(text.lower())
    found: set[str] = set()
    for match in _NUMBER_YEAR.finditer(lowered):
        number = match.group(1).replace(".", "")
        year = match.group(2)
        found.add(f"{number}/{year}")
        if expanded := _expand_year(year):
            found.add(f"{number}/{expanded}")
        elif len(year) == 4:
            found.add(f"{number}/{year[2:]}")
    for match in _ARTICLE_REF.finditer(lowered):
        found.add(f"art{match.group(1)}{match.group(2) or ''}")
    return found


@dataclass(frozen=True, slots=True)
class Bm25Hit:
    chunk_id: str
    score: float


class Bm25Index:
    """BM25 Okapi sobre os chunks indexados. Seguro para uso concorrente."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._postings: dict[str, dict[str, int]] = defaultdict(dict)
        self._doc_len: dict[str, int] = {}
        self._doc_tokens: dict[str, frozenset[str]] = {}
        self._avg_len: float = 0.0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._doc_len)

    def rebuild(self, documents: list[tuple[str, str]]) -> None:
        """Reconstroi o indice inteiro a partir de ``(chunk_id, texto)``."""
        postings: dict[str, dict[str, int]] = defaultdict(dict)
        doc_len: dict[str, int] = {}
        doc_tokens: dict[str, frozenset[str]] = {}

        for chunk_id, text in documents:
            tokens = tokenize(text)
            if not tokens:
                continue
            counts = Counter(tokens)
            for term, frequency in counts.items():
                postings[term][chunk_id] = frequency
            doc_len[chunk_id] = len(tokens)
            doc_tokens[chunk_id] = frozenset(counts)

        with self._lock:
            self._postings = postings
            self._doc_len = doc_len
            self._doc_tokens = doc_tokens
            self._avg_len = (sum(doc_len.values()) / len(doc_len)) if doc_len else 0.0

        logger.info(
            "bm25_index_rebuilt",
            extra={"chunks": len(doc_len), "terms": len(postings)},
        )

    def clear(self) -> None:
        self.rebuild([])

    def contains_tokens(self, chunk_id: str, tokens: set[str]) -> bool:
        """True se o chunk contem pelo menos um dos tokens informados."""
        if not tokens:
            return False
        with self._lock:
            stored = self._doc_tokens.get(chunk_id)
        return bool(stored and stored & tokens)

    def search(self, query: str, limit: int) -> list[Bm25Hit]:
        """Retorna os ``limit`` chunks com maior score BM25."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        with self._lock:
            total = len(self._doc_len)
            if total == 0 or self._avg_len <= 0:
                return []
            postings = self._postings
            doc_len = self._doc_len
            avg_len = self._avg_len

            scores: dict[str, float] = defaultdict(float)
            for term in set(query_tokens):
                term_postings = postings.get(term)
                if not term_postings:
                    continue
                document_frequency = len(term_postings)
                idf = math.log(
                    1 + (total - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                for chunk_id, frequency in term_postings.items():
                    length_norm = 1 - _B + _B * (doc_len[chunk_id] / avg_len)
                    scores[chunk_id] += idf * (
                        frequency * (_K1 + 1) / (frequency + _K1 * length_norm)
                    )

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [Bm25Hit(chunk_id=chunk_id, score=score) for chunk_id, score in ranked[:limit]]
