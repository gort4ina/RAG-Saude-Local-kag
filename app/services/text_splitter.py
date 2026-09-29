"""Divisao de texto orientada a estrutura de documentos regulatorios.

UNIDADE DE MEDIDA: este modulo conta CARACTERES, nao tokens. Nao ha
tokenizador real no pipeline. Os defaults (1900 / 285 caracteres)
correspondem a aproximadamente 500 / 75 tokens em portugues usando a
referencia de ~3.8 caracteres por token. Se um tokenizador for adicionado
no futuro, recalibre esses valores.

Ordem de prioridade dos cortes:

1. titulos Markdown (``#``, ``##``, ...);
2. ANEXO;
3. CAPITULO;
4. SECAO;
5. Art. N;
6. paragrafos (linha em branco);
7. frases;
8. corte duro por tamanho, com sobreposicao.

Um chunk nunca termina imediatamente apos o cabecalho de um artigo: o
numero do artigo permanece colado ao seu conteudo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

# --------------------------------------------------------------------------
# Padroes estruturais
# --------------------------------------------------------------------------

_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_ANNEX = re.compile(r"^\s*(ANEXO\s+[IVXLCDM\d]+[^\n]{0,80}|ANEXO\b[^\n]{0,80})$", re.IGNORECASE)
_CHAPTER = re.compile(r"^\s*(CAP[IÍ]TULO\s+[IVXLCDM\d]+[^\n]{0,100})$", re.IGNORECASE)
_SECTION = re.compile(r"^\s*(SE[ÇC][ÃA]O\s+[IVXLCDM\d]+[^\n]{0,100})$", re.IGNORECASE)
_TITLE_BLOCK = re.compile(r"^\s*(T[IÍ]TULO\s+[IVXLCDM\d]+[^\n]{0,100})$", re.IGNORECASE)
_ARTICLE = re.compile(r"^\s*(Art\.?\s*\d+\s*[ºo°]?(?:\s*-\s*[A-Z])?)", re.IGNORECASE)
_PARAGRAPH = re.compile(r"^\s*(§\s*\d+\s*[ºo°]?|Par[áa]grafo\s+[úu]nico)", re.IGNORECASE)
_INCISO = re.compile(r"^\s*([IVXLCDM]{1,7})\s*[-–—]\s+")
_ALINEA = re.compile(r"^\s*([a-z])\)\s+")

# Fim de frase: ponto/interrogacao/exclamacao seguido de espaco e maiuscula.
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-Ý0-9§])")

_ARTICLE_ONLY = re.compile(r"^\s*Art\.?\s*\d+\s*[ºo°]?(?:\s*-\s*[A-Z])?\s*[.\-–—]?\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class StructuralContext:
    """Posicao do trecho dentro da hierarquia do documento."""

    title: str | None = None
    annex: str | None = None
    chapter: str | None = None
    section: str | None = None
    article: str | None = None
    paragraph: str | None = None
    inciso: str | None = None
    alinea: str | None = None

    def item_label(self) -> str | None:
        """Rotulo mais especifico disponivel (artigo, paragrafo, inciso...)."""
        parts = [
            part
            for part in (self.article, self.paragraph, self.inciso, self.alinea)
            if part
        ]
        return ", ".join(parts) if parts else None


@dataclass(frozen=True, slots=True)
class StructuredChunk:
    """Um trecho pronto para indexacao."""

    body: str
    context: StructuralContext


@dataclass(frozen=True, slots=True)
class _Block:
    """Unidade atomica de texto delimitada por um marcador estrutural."""

    text: str
    context: StructuralContext
    hard_boundary: bool


def _normalize(text: str) -> str:
    """Remove espacos supérfluos preservando quebras de paragrafo."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00ad", "")  # hifen de quebra suave dos PDFs
    lines = [line.strip() for line in text.split("\n")]
    normalized: list[str] = []
    blank = False
    for line in lines:
        if line:
            normalized.append(re.sub(r"[ \t]{2,}", " ", line))
            blank = False
        elif not blank and normalized:
            normalized.append("")
            blank = True
    while normalized and normalized[-1] == "":
        normalized.pop()
    return "\n".join(normalized)


def _update_context(line: str, current: StructuralContext) -> tuple[StructuralContext, bool]:
    """Atualiza a hierarquia a partir de uma linha. Retorna (contexto, e_fronteira)."""
    heading = _MARKDOWN_HEADING.match(line)
    if heading:
        level = len(heading.group(1))
        text = heading.group(2).strip()
        if level == 1:
            return StructuralContext(title=text), True
        if level == 2:
            return replace(
                current, chapter=text, section=None, article=None,
                paragraph=None, inciso=None, alinea=None,
            ), True
        return replace(
            current, section=text, article=None, paragraph=None,
            inciso=None, alinea=None,
        ), True

    if match := _ANNEX.match(line):
        return StructuralContext(title=current.title, annex=match.group(1).strip()), True

    if match := _TITLE_BLOCK.match(line):
        return replace(
            current, chapter=match.group(1).strip(), section=None, article=None,
            paragraph=None, inciso=None, alinea=None,
        ), True

    if match := _CHAPTER.match(line):
        return replace(
            current, chapter=match.group(1).strip(), section=None, article=None,
            paragraph=None, inciso=None, alinea=None,
        ), True

    if match := _SECTION.match(line):
        return replace(
            current, section=match.group(1).strip(), article=None,
            paragraph=None, inciso=None, alinea=None,
        ), True

    if match := _ARTICLE.match(line):
        article = re.sub(r"\s+", " ", match.group(1).strip()).rstrip(".")
        return replace(
            current, article=article, paragraph=None, inciso=None, alinea=None
        ), True

    if match := _PARAGRAPH.match(line):
        return replace(
            current,
            paragraph=re.sub(r"\s+", " ", match.group(1).strip()),
            inciso=None,
            alinea=None,
        ), False

    if match := _INCISO.match(line):
        return replace(current, inciso=f"inciso {match.group(1)}", alinea=None), False

    if match := _ALINEA.match(line):
        return replace(current, alinea=f"alinea {match.group(1)}"), False

    return current, False


def _build_blocks(text: str) -> list[_Block]:
    """Quebra o texto em blocos delimitados por marcadores estruturais."""
    blocks: list[_Block] = []
    buffer: list[str] = []
    context = StructuralContext()
    block_context = context
    pending_boundary = False

    def flush() -> None:
        nonlocal buffer, block_context
        content = "\n".join(buffer).strip()
        if content:
            blocks.append(
                _Block(text=content, context=block_context, hard_boundary=pending_boundary)
            )
        buffer = []

    for line in text.split("\n"):
        new_context, is_boundary = _update_context(line, context)
        if is_boundary and buffer:
            flush()
            block_context = new_context
            pending_boundary = True
        elif not buffer:
            block_context = new_context
        context = new_context
        if line or buffer:
            buffer.append(line)

    flush()
    return blocks


def _split_oversized(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    """Divide um bloco maior que o limite: paragrafo -> frase -> corte duro."""
    if len(text) <= chunk_chars:
        return [text]

    units: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= chunk_chars:
            units.append(paragraph)
            continue
        for sentence in _SENTENCE_END.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= chunk_chars:
                units.append(sentence)
            else:
                step = max(1, chunk_chars - overlap_chars)
                for start in range(0, len(sentence), step):
                    piece = sentence[start : start + chunk_chars].strip()
                    if piece:
                        units.append(piece)

    pieces: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if len(candidate) <= chunk_chars or not current:
            current = candidate
            continue
        pieces.append(current)
        tail = current[-overlap_chars:] if overlap_chars else ""
        # Nunca reabre um chunk apenas com o cabecalho "Art. N".
        current = f"{tail}\n\n{unit}".strip() if tail and not _ARTICLE_ONLY.match(tail) else unit
    if current:
        pieces.append(current)
    return pieces


def split_structured(
    text: str,
    *,
    chunk_chars: int = 1_900,
    overlap_chars: int = 285,
) -> list[StructuredChunk]:
    """Divide o texto preservando a estrutura regulatoria.

    ``chunk_chars`` e ``overlap_chars`` sao contados em CARACTERES.
    """
    if chunk_chars <= 0:
        raise ValueError("chunk_chars deve ser positivo")
    if overlap_chars < 0 or overlap_chars >= chunk_chars:
        raise ValueError("overlap_chars deve estar entre 0 e chunk_chars - 1")

    normalized = _normalize(text)
    if not normalized:
        return []

    chunks: list[StructuredChunk] = []
    buffer = ""
    buffer_context = StructuralContext()

    def emit(content: str, context: StructuralContext) -> None:
        content = content.strip()
        if content and not _ARTICLE_ONLY.match(content):
            chunks.append(StructuredChunk(body=content, context=context))

    for block in _build_blocks(normalized):
        for piece in _split_oversized(block.text, chunk_chars, overlap_chars):
            if not buffer:
                buffer, buffer_context = piece, block.context
                continue
            # Um cabecalho de artigo isolado sempre segue com o proximo bloco.
            if _ARTICLE_ONLY.match(buffer):
                buffer = f"{buffer}\n{piece}"
                continue
            candidate = f"{buffer}\n\n{piece}"
            if len(candidate) <= chunk_chars and not block.hard_boundary:
                buffer = candidate
            else:
                emit(buffer, buffer_context)
                buffer, buffer_context = piece, block.context

    emit(buffer, buffer_context)
    return chunks


def split_text(text: str, chunk_size: int = 1_900, overlap: int = 285) -> list[str]:
    """Compatibilidade: devolve apenas os corpos dos chunks estruturados."""
    return [
        chunk.body
        for chunk in split_structured(
            text, chunk_chars=chunk_size, overlap_chars=overlap
        )
    ]
