"""Normalizacao compartilhada de texto (acento, caixa, espaco).

Usada pelo KAG, pelos aliases de entidade e pelo harness de avaliacao.
Uma unica funcao evita o padrao de cada modulo reinventar ``NFKD``.
"""

from __future__ import annotations

import re
import unicodedata


_WS = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_text(text: str) -> str:
    """Caixa baixa, sem acento, espacos colapsados."""
    return _WS.sub(" ", strip_accents(text).casefold()).strip()


def normalize_entity_name(name: str) -> str:
    """Chave canonica de um no do grafo (AFE ≡ Afe ≡ afe)."""
    return normalize_text(name)
