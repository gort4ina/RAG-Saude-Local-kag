"""Extracao de metadados regulatorios a partir do texto do documento.

Principios:

- nada e inventado. Campo nao encontrado fica ``None``;
- vigencia e revogacao NUNCA sao deduzidas do nome ou do conteudo. Um documento
  so recebe ``vigente_confirmada`` ou ``revogada_confirmada`` quando um arquivo
  lateral de metadados confirmado (``<arquivo>.meta.json``) declarar isso;
- o default e ``vigencia_nao_verificada``;
- frases que tentam instruir o assistente sao descartadas antes da extracao dos
  demais metadados.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from app.services.safety import sanitize_context

logger = logging.getLogger(__name__)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))

STATUS_IN_FORCE = "vigente_confirmada"
STATUS_REVOKED = "revogada_confirmada"
STATUS_UNVERIFIED = "vigencia_nao_verificada"

VALID_STATUSES = {STATUS_IN_FORCE, STATUS_REVOKED, STATUS_UNVERIFIED}

STATUS_LABELS = {
    STATUS_IN_FORCE: "Vigencia confirmada",
    STATUS_REVOKED: "Revogada (confirmado)",
    STATUS_UNVERIFIED: "Vigencia nao verificada",
}

# Orgaos emissores reconhecidos, do mais especifico para o mais generico.
_AUTHORITIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ANVISA", re.compile(r"\bANVISA\b|Ag[eê]ncia Nacional de Vigil[aâ]ncia Sanit[aá]ria", re.IGNORECASE)),
    ("Ministerio da Saude", re.compile(r"Minist[eé]rio da Sa[uú]de", re.IGNORECASE)),
    ("CFF", re.compile(r"\bCFF\b|Conselho Federal de Farm[aá]cia", re.IGNORECASE)),
    ("CRF", re.compile(r"\bCRF(?:[-/]?[A-Z]{2})?\b|Conselho Regional de Farm[aá]cia", re.IGNORECASE)),
    ("Vigilancia Sanitaria", re.compile(r"Vigil[aâ]ncia Sanit[aá]ria(?! da Anvisa)", re.IGNORECASE)),
)

# Cobre as duas formas usuais de citar uma norma:
#   "RDC 67/2007", "Portaria 344/98", "IN 5/2021"
#   "RESOLUCAO DA DIRETORIA COLEGIADA - RDC No 67, DE 8 DE OUTUBRO DE 2007"
_REGULATION_NUMBER = re.compile(
    r"\b(RDC|RESOLU[ÇC][ÃA]O(?:\s+DA\s+DIRETORIA\s+COLEGIADA)?|PORTARIA"
    r"|INSTRU[ÇC][ÃA]O\s+NORMATIVA|IN|LEI|DECRETO)"
    r"(?:\s*[-–]\s*RDC)?"
    r"[\s\-–,]*(?:n[ºo°.]*\s*)?(\d{1,6}(?:\.\d{3})*)"
    r"(?P<tail>"
    r"\s*[/-]\s*\d{2,4}"
    r"|\s*,?\s*de\s+\d{1,2}\s+de\s+[a-zç]+\s+de\s+\d{4}"
    r"|\s*,?\s*de\s+\d{4}"
    r")?",
    re.IGNORECASE,
)

_YEAR_IN_TAIL = re.compile(r"(\d{2,4})\s*$")

_TYPE_CANONICAL = {
    "rdc": "RDC",
    "resolucao": "RDC",
    "resolucao da diretoria colegiada": "RDC",
    "portaria": "Portaria",
    "instrucao normativa": "IN",
    "in": "IN",
    "lei": "Lei",
    "decreto": "Decreto",
}

_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

_DATE_LONG = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})\b", re.IGNORECASE
)
_DATE_SHORT = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

_EFFECTIVE = re.compile(
    r"entra\s+em\s+vigor[^.\n]{0,120}", re.IGNORECASE
)
_URL = re.compile(r"https?://[^\s<>\")]+")
_VERSION = re.compile(
    r"\b(?:vers[ãa]o|revis[ãa]o|rev\.?)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class RegulatoryMetadata:
    """Metadados de um documento regulatorio. ``None`` = nao disponivel."""

    document_id: str
    filename: str
    content_hash: str
    authority: str | None = None
    regulation_number: str | None = None
    publication_date: str | None = None
    effective_date: str | None = None
    status: str = STATUS_UNVERIFIED
    document_title: str | None = None
    source_url: str | None = None
    document_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_date(day: str, month: str | int, year: str) -> str | None:
    try:
        month_number = int(month) if str(month).isdigit() else _MONTHS.get(str(month).lower(), 0)
        if not month_number:
            return None
        return f"{int(year):04d}-{month_number:02d}-{int(day):02d}"
    except (TypeError, ValueError):
        return None


def _find_date(text: str) -> str | None:
    if match := _DATE_LONG.search(text):
        return _normalize_date(match.group(1), match.group(2), match.group(3))
    if match := _DATE_SHORT.search(text):
        return _normalize_date(match.group(1), match.group(2), match.group(3))
    return None


def _find_authority(text: str) -> str | None:
    for label, pattern in _AUTHORITIES:
        if pattern.search(text):
            return label
    return None


def _find_regulation_number(text: str) -> str | None:
    match = _REGULATION_NUMBER.search(text)
    if not match:
        return None
    raw_type = strip_accents(re.sub(r"\s+", " ", match.group(1)).strip().lower())
    canonical = _TYPE_CANONICAL.get(raw_type, match.group(1).upper())
    number = match.group(2)

    tail = match.group("tail") or ""
    year_match = _YEAR_IN_TAIL.search(tail)
    if not year_match:
        return f"{canonical} {number}"

    year = year_match.group(1)
    if len(year) == 2:
        year = f"19{year}" if int(year) > 50 else f"20{year}"
    return f"{canonical} {number}/{year}"


def _trusted_text(text: str) -> str:
    """Texto sem as frases que tentam instruir o assistente.

    Nenhum metadado derivado pode sair de uma frase que tenta instruir o
    assistente. O status regulatorio nem sequer e derivado do texto: ele exige
    confirmacao manual no sidecar.
    """
    cleaned, removed = sanitize_context(text)
    if removed:
        logger.warning("metadata_injection_ignored", extra={"sentences": removed})
    return cleaned


def content_hash(content: bytes) -> str:
    """Hash estavel do conteudo bruto, usado para deduplicacao."""
    return hashlib.sha256(content).hexdigest()


def sidecar_path(upload_dir: Path, filename: str) -> Path:
    """Caminho do arquivo lateral de metadados confirmados."""
    return upload_dir / f"{Path(filename).name}.meta.json"


def load_sidecar(path: Path) -> dict[str, Any]:
    """Le metadados confirmados manualmente. Nunca levanta excecao."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("metadata_sidecar_unreadable", extra={"path": path.name})
        return {}
    return data if isinstance(data, dict) else {}


def extract_metadata(
    text: str,
    *,
    document_id: str,
    filename: str,
    raw_content: bytes,
    confirmed: dict[str, Any] | None = None,
) -> RegulatoryMetadata:
    """Deriva metadados do inicio do documento e aplica os confirmados.

    ``text`` deve ser o texto das primeiras paginas: cabecalho, ementa e
    preambulo concentram autoria, numero e datas.
    """
    text = _trusted_text(text)
    head = text[:6_000]

    publication_date = _find_date(head)
    effective_date = None
    if effective_match := _EFFECTIVE.search(text):
        effective_date = _find_date(effective_match.group(0))

    title = None
    for line in head.split("\n"):
        candidate = line.strip().lstrip("# ").strip()
        if len(candidate) >= 12:
            title = candidate[:200]
            break

    metadata = RegulatoryMetadata(
        document_id=document_id,
        filename=filename,
        content_hash=content_hash(raw_content),
        authority=_find_authority(head),
        regulation_number=_find_regulation_number(head),
        publication_date=publication_date,
        effective_date=effective_date,
        # Vigencia e revogacao sao afirmacoes regulatorias externas ao
        # conteudo. Somente apply_confirmed() pode sobrescrever este default.
        status=STATUS_UNVERIFIED,
        document_title=title,
        source_url=(match.group(0) if (match := _URL.search(head)) else None),
        document_version=(match.group(1) if (match := _VERSION.search(head)) else None),
    )
    return apply_confirmed(metadata, confirmed or {})


def apply_confirmed(
    metadata: RegulatoryMetadata, confirmed: dict[str, Any]
) -> RegulatoryMetadata:
    """Sobrepoe os campos declarados manualmente como confirmados."""
    if not confirmed:
        return metadata

    overrides: dict[str, Any] = {}
    for key in (
        "authority",
        "regulation_number",
        "publication_date",
        "effective_date",
        "document_title",
        "source_url",
        "document_version",
    ):
        value = confirmed.get(key)
        if isinstance(value, str) and value.strip():
            overrides[key] = value.strip()

    status = confirmed.get("status")
    if isinstance(status, str) and status.strip() in VALID_STATUSES:
        overrides["status"] = status.strip()
    elif status:
        logger.warning("metadata_invalid_status", extra={"status": str(status)[:40]})

    return replace(metadata, **overrides) if overrides else metadata


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or STATUS_UNVERIFIED, STATUS_LABELS[STATUS_UNVERIFIED])


def to_chroma_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """ChromaDB nao aceita ``None``: chaves vazias sao removidas."""
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != ""
    }
