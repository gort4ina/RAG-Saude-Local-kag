"""Front-matter YAML minimo para Markdown/TXT.

Nao puxa PyYAML: o formato aceito e o subset ``chave: valor`` entre
delimitadores ``---``. Status regulatorio so e aceito se estiver na
lista canonica — o mesmo contrato do sidecar ``.meta.json``.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.metadata import VALID_STATUSES

_BLOCK = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.+?)\s*$")

_ALLOWED = {
    "authority",
    "regulation_number",
    "publication_date",
    "effective_date",
    "document_title",
    "source_url",
    "document_version",
    "status",
}


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Separa metadados do corpo. Sem bloco, devolve ``({}, text)``."""
    match = _BLOCK.match(text)
    if not match:
        return {}, text

    raw = match.group(1)
    body = text[match.end() :]
    parsed: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = _LINE.match(stripped)
        if not item:
            continue
        key = item.group(1).strip()
        value = item.group(2).strip().strip("\"'")
        if key not in _ALLOWED or not value:
            continue
        if key == "status" and value not in VALID_STATUSES:
            continue
        parsed[key] = value
    return parsed, body
