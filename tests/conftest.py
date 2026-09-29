"""Configuracao global dos testes.

As variaveis de ambiente sao fixadas ANTES de qualquer import de ``app``,
porque ``app.config`` chama ``load_dotenv()`` na importacao e ``app.main``
resolve as configuracoes no nivel do modulo. Sem isso, o ``.env`` local do
desenvolvedor mudaria o resultado da suite.

``load_dotenv()`` nao sobrescreve variaveis ja definidas, entao definir aqui
tem prioridade sobre o arquivo ``.env``.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_TEST_DATA = Path(tempfile.gettempdir()) / "rag-tests-data"
_TEST_DATA.mkdir(parents=True, exist_ok=True)

os.environ.update(
    {
        "APP_NAME": "RAG Regulatorio Farmaceutico",
        "APP_ENV": "test",
        "LOG_LEVEL": "DEBUG",
        # Sem fixar estes valores a suite herdaria o `.env` do desenvolvedor e
        # tentaria falar com o Postgres real durante o startup da aplicacao.
        "DATABASE_URL": f"sqlite+aiosqlite:///{(_TEST_DATA / 'rag-test.db').as_posix()}",
        "BOOTSTRAP_ADMIN_PASSWORD": "",
        "JWT_SECRET": "segredo-de-teste-com-mais-de-32-caracteres-aqui",
        "COOKIE_SECURE": "false",
        "ALLOWED_HOSTS": "*",
        "ALLOWED_ORIGINS": "http://localhost:4200",
        "DEFAULT_RATE_LIMIT": "100000/minute",
        "REFRESH_RATE_LIMIT": "100000/minute",
        "ADMIN_RATE_LIMIT": "100000/minute",
        "OLLAMA_BASE_URL": "http://ollama-de-teste:11434",
        "OLLAMA_CHAT_MODEL": "qwen2.5:3b",
        "OLLAMA_EMBEDDING_MODEL": "embeddinggemma",
        "OLLAMA_CONTEXT_LENGTH": "4096",
        "OLLAMA_NUM_PREDICT": "450",
        "OLLAMA_TEMPERATURE": "0.1",
        "CHROMA_PATH": str(_TEST_DATA / "chroma"),
        "UPLOAD_PATH": str(_TEST_DATA / "uploads"),
        "COLLECTION_NAME": "colecao_de_teste",
        "RETRIEVAL_CANDIDATES": "12",
        "MAX_CONTEXT_CHUNKS": "4",
        "MIN_RELEVANCE_SCORE": "0.45",
        "HYBRID_SEARCH_ENABLED": "true",
        "CHUNK_CHARS": "1900",
        "CHUNK_OVERLAP_CHARS": "285",
        "MAX_UPLOAD_MB": "15",
        "CHAT_RATE_LIMIT": "10000/minute",
        "UPLOAD_RATE_LIMIT": "10000/minute",
        "LOGIN_RATE_LIMIT": "10000/minute",
        "KAG_ENABLED": "true",
        "KAG_GRAPH_BACKEND": "postgres",
    }
)


@pytest.fixture(autouse=True)
def emit_real_log_records() -> Iterator[None]:
    """Faz a suite exercitar a criacao e a formatacao reais dos logs.

    Sem um handler ativo em DEBUG o ``logger.info()`` sai antes de montar o
    ``LogRecord``, e um ``extra`` com chave reservada (``filename``,
    ``module``...) so estouraria em producao. Formatar de verdade num buffer
    tambem cobre valores que nao serializam em JSON.
    """
    from app.logging_config import JsonFormatter

    root = logging.getLogger()
    handler = logging.StreamHandler(io.StringIO())
    handler.setFormatter(JsonFormatter())
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
