"""Testes do logging estruturado.

O caso que motivou este arquivo: ``logger.info(..., extra={"filename": ...})``
derrubou o upload em producao com ``KeyError: "Attempt to overwrite 'filename'
in LogRecord"``. A suite nao pegou porque rodava sem handler, e o
``logger.info`` retornava antes de montar o registro.
"""

from __future__ import annotations

import ast
import io
import json
import logging
from pathlib import Path

import pytest

from app.logging_config import (
    RESERVED_RECORD_KEYS,
    JsonFormatter,
    configure_logging,
    get_request_id,
    new_request_id,
    set_request_id,
)

_APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _extra_keys_in_source(path: Path) -> list[tuple[int, str]]:
    """Chaves literais passadas em ``extra={...}`` de cada chamada de log."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.append((key.lineno, key.value))
    return found


def test_no_log_extra_uses_a_reserved_record_key() -> None:
    offenders = [
        f"{path.relative_to(_APP_DIR.parent)}:{line} usa extra={{'{key}': ...}}"
        for path in sorted(_APP_DIR.rglob("*.py"))
        for line, key in _extra_keys_in_source(path)
        if key in RESERVED_RECORD_KEYS
    ]
    assert offenders == [], (
        "Chaves reservadas do LogRecord fazem o logging levantar KeyError em "
        "tempo de execucao. Renomeie (ex.: 'filename' -> 'document_filename'): "
        + "; ".join(offenders)
    )


def test_reserved_key_in_extra_would_break_logging() -> None:
    """Prova que a regra acima protege de um erro real, nao teorico."""
    logger = logging.getLogger("test_reserved")
    with pytest.raises(KeyError):
        logger.info("boom", extra={"filename": "documento.pdf"})


def test_formatter_emits_json_with_request_id_and_extras() -> None:
    set_request_id("req-123")
    record = logging.LogRecord(
        name="app.teste",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="document_indexed",
        args=(),
        exc_info=None,
    )
    record.document_filename = "RDC-67.md"
    record.chunks = 18

    payload = json.loads(JsonFormatter().format(record))

    assert payload["msg"] == "document_indexed"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-123"
    assert payload["document_filename"] == "RDC-67.md"
    assert payload["chunks"] == 18


def test_formatter_never_leaks_record_internals() -> None:
    record = logging.LogRecord(
        name="app.teste",
        level=logging.INFO,
        pathname="/app/app/services/rag_service.py",
        lineno=42,
        msg="ok",
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert "pathname" not in payload
    assert "levelno" not in payload


def test_formatter_serializes_values_that_are_not_json_native() -> None:
    record = logging.LogRecord(
        name="app.teste",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="path_logged",
        args=(),
        exc_info=None,
    )
    record.origem = Path("/app/data/chroma")

    payload = json.loads(JsonFormatter().format(record))

    assert "chroma" in payload["origem"]


def test_request_id_is_new_for_each_call() -> None:
    first = new_request_id()
    second = new_request_id()

    assert first != second
    assert get_request_id() == second


def test_configure_logging_replaces_handlers_with_a_single_json_handler() -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    try:
        configure_logging("INFO")

        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.INFO
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)


def test_end_to_end_log_line_is_valid_json() -> None:
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app.teste.e2e")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    set_request_id("req-e2e")
    try:
        logger.info("chat_completed", extra={"grounded": True, "duration_ms": 12})
    finally:
        logger.removeHandler(handler)

    payload = json.loads(buffer.getvalue().splitlines()[-1])

    assert payload["grounded"] is True
    assert payload["duration_ms"] == 12
    assert payload["request_id"] == "req-e2e"
