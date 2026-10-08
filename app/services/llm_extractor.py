"""Extrator KAG assistido por LLM.

O contrato ``RelationExtractor`` e sincrono. A chamada ao modelo e
assincrona, entao este modulo expoe ``extract_async``. O
``KnowledgeGraphService`` usa o dicionario no caminho sincrono e, se
``KAG_LLM_EXTRACTOR=true``, acrescenta as arestas do LLM na mesma
transacao — sempre com ``confidence < 1.0`` e
``pendente_de_validacao``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from typing import Any

from app.services.knowledge_graph import ExtractedRelation
from app.services.metadata import RegulatoryMetadata
from app.services.text_splitter import StructuredChunk

logger = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"\{[^{}]+\}")

_ALLOWED_RELATIONS = {
    "trata_de",
    "relacionado_a",
    "emitida_por",
    "identifica",
    "regula",
    "revoga",
    "altera",
}

_ALLOWED_KINDS = {
    "documento",
    "fonte",
    "tema",
    "norma",
    "conceito",
    "evidencia",
}


def parse_llm_relations(raw: str, *, source_span: str | None) -> list[ExtractedRelation]:
    """Interpreta JSON/JSONL devolvido pelo modelo. Descartar o resto."""
    relations: list[ExtractedRelation] = []
    for match in _JSON_OBJECT.finditer(raw):
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        relation = str(payload.get("relation") or "").strip()
        target_kind = str(payload.get("target_kind") or payload.get("object_kind") or "").strip()
        target_name = str(payload.get("target") or payload.get("object") or "").strip()
        source_kind = str(payload.get("source_kind") or "").strip() or None
        source_name = str(payload.get("source") or payload.get("subject") or "").strip() or None
        if relation not in _ALLOWED_RELATIONS or not target_name:
            continue
        if target_kind not in _ALLOWED_KINDS:
            target_kind = "tema"
        try:
            confidence = float(payload.get("confidence", 0.55))
        except (TypeError, ValueError):
            confidence = 0.55
        confidence = min(0.85, max(0.1, confidence))
        relations.append(
            ExtractedRelation(
                relation=relation,
                target_kind=target_kind,
                target_name=target_name[:255],
                confidence=confidence,
                source_span=source_span,
                extracted_by="llm",
                source_kind=source_kind if source_kind in _ALLOWED_KINDS else None,
                source_name=source_name[:255] if source_name else None,
            )
        )
        if len(relations) >= 3:
            break
    return relations


class LLMAssistedRelationExtractor:
    """Segundo passo opcional: pede ao Ollama ate 3 arestas por chunk."""

    name = "llm"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def extract(
        self,
        *,
        metadata: RegulatoryMetadata,
        chunks: list[StructuredChunk],
    ) -> Iterable[ExtractedRelation]:
        """Caminho sincrono: vazio. O LLM so roda em ``extract_async``."""
        del metadata, chunks
        return []

    async def extract_async(
        self,
        *,
        metadata: RegulatoryMetadata,
        chunks: list[StructuredChunk],
    ) -> list[ExtractedRelation]:
        if self._client is None:
            return []
        collected: list[ExtractedRelation] = []
        for chunk in chunks[:8]:
            span = chunk.context.item_label() or chunk.context.section or "trecho"
            prompt = (
                "Extraia no maximo 3 relacoes regulatorias do trecho abaixo. "
                "Responda SOMENTE com JSONL, um objeto por linha, chaves: "
                "relation, source, source_kind, target, target_kind, confidence. "
                f"Norma: {metadata.regulation_number or metadata.filename}. "
                f"Trecho ({span}):\n{chunk.body[:1200]}"
            )
            try:
                result = await self._client.chat(
                    "Voce extrai relacoes. Nunca invente artigos. JSONL puro.",
                    prompt,
                )
                raw = getattr(result, "content", "") or ""
            except Exception:
                logger.warning("kag_llm_extract_failed", extra={"span": span})
                continue
            collected.extend(parse_llm_relations(raw, source_span=span))
        return collected
