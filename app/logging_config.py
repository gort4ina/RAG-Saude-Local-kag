"""Configuracao de logs estruturados para a RAG.

Cada linha e um JSON com timestamp, nivel, mensagem, request_id e extras
uteis (modelo, latencia, tamanho de contexto). Assim os logs sao faceis
de filtrar em qualquer coletor (Docker logs, Loki, CloudWatch etc.).
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from typing import Any

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def new_request_id() -> str:
    """Cria e fixa um request-id curto no contexto atual."""
    request_id = uuid.uuid4().hex[:12]
    _request_id.set(request_id)
    return request_id


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()


#: Atributos que o proprio ``LogRecord`` ja ocupa. Usar qualquer um deles em
#: ``logger.info(..., extra={...})`` faz o logging levantar ``KeyError`` e
#: derruba a requisicao inteira, entao nomes de campo precisam ser distintos
#: (ex.: ``document_filename`` em vez de ``filename``).
RESERVED_RECORD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Formata cada registro como JSON compacto."""

    RESERVED = RESERVED_RECORD_KEYS

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": get_request_id(),
        }
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configura o logger root para emitir JSON no stdout."""
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())

    for noisy in ("httpx", "httpcore", "chromadb.telemetry"):
        logging.getLogger(noisy).setLevel("WARNING")
