"""Tracing opcional. Sem exporter configurado, e um no-op.

Nao adiciona ``opentelemetry`` como dependencia obrigatoria: se o pacote
nao estiver instalado ou ``OTEL_EXPORTER_OTLP_ENDPOINT`` estiver vazio,
``span()`` so registra um log de debug. Assim a observabilidade fina
entra sem custo de startup no MVP.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


def tracing_enabled() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Abre um span OTel se possivel; caso contrario cronometra em log."""
    if not tracing_enabled():
        yield
        return

    started = time.perf_counter()
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]

        tracer = trace.get_tracer("rag-regulatorio")
        with tracer.start_as_current_span(name) as current:
            for key, value in attributes.items():
                if value is not None:
                    current.set_attribute(key, value)
            yield
        return
    except Exception:
        logger.debug("otel_span_unavailable", extra={"span": name})

    try:
        yield
    finally:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.debug(
            "span_fallback",
            extra={"span": name, "elapsed_ms": elapsed, **attributes},
        )
