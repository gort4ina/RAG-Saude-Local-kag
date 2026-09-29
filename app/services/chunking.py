"""Montagem final dos chunks: estrutura + metadados + cabecalho.

Cada chunk indexado recebe um cabecalho legivel antes do corpo. O cabecalho
entra no texto vetorizado de proposito: ele carrega o numero da norma, o
orgao e o artigo, que sao exatamente os termos usados nas perguntas
regulatorias ("o que diz o art. 5 da RDC 67/2007?").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.metadata import RegulatoryMetadata, status_label, to_chroma_metadata
from app.services.text_splitter import StructuralContext, split_structured

_UNKNOWN = "nao informado"


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    """Chunk pronto para virar embedding e registro no ChromaDB."""

    text: str
    body: str
    metadata: dict[str, Any]


def build_chunk_header(
    metadata: RegulatoryMetadata,
    context: StructuralContext,
    page: int,
) -> str:
    """Cabecalho fixo de 8 campos. Campos ausentes ficam explicitos."""
    document = metadata.document_title or metadata.filename
    chapter = context.chapter or (context.annex if context.annex else None)
    return "\n".join(
        (
            f"Documento: {document}",
            f"Orgao: {metadata.authority or _UNKNOWN}",
            f"Norma: {metadata.regulation_number or _UNKNOWN}",
            f"Capitulo: {chapter or _UNKNOWN}",
            f"Secao: {context.section or _UNKNOWN}",
            f"Artigo ou item: {context.item_label() or _UNKNOWN}",
            f"Pagina: {page}",
            f"Status informado: {status_label(metadata.status)}",
        )
    )


def build_chunks(
    pages: list[tuple[int, str]],
    metadata: RegulatoryMetadata,
    *,
    tenant_id: str,
    chunk_chars: int,
    overlap_chars: int,
) -> list[IndexedChunk]:
    """Divide cada pagina e devolve os chunks com cabecalho e metadados.

    ``pages`` e uma lista de ``(numero_da_pagina, texto)``.
    """
    chunks: list[IndexedChunk] = []
    counter = 0

    for page_number, page_text in pages:
        for piece in split_structured(
            page_text, chunk_chars=chunk_chars, overlap_chars=overlap_chars
        ):
            counter += 1
            header = build_chunk_header(metadata, piece.context, page_number)
            payload: dict[str, Any] = {
                "tenant_id": tenant_id,
                "document_id": metadata.document_id,
                "filename": metadata.filename,
                "page": page_number,
                "chunk": counter,
                "authority": metadata.authority,
                "regulation_number": metadata.regulation_number,
                "publication_date": metadata.publication_date,
                "effective_date": metadata.effective_date,
                "status": metadata.status,
                "article": piece.context.item_label(),
                "section": piece.context.section or piece.context.chapter,
                "source_url": metadata.source_url,
                "document_version": metadata.document_version,
                "content_hash": metadata.content_hash,
            }
            chunks.append(
                IndexedChunk(
                    text=f"{header}\n\n{piece.body}",
                    body=piece.body,
                    metadata=to_chroma_metadata(payload),
                )
            )
    return chunks
