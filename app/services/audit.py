"""Persistência append-only dos eventos relevantes para revisão humana.

Duas propriedades definem o comportamento em produção:

- **Minimização (LGPD, art. 6º V):** o texto integral da pergunta só é
  guardado quando ``AUDIT_PERSIST_QUERY_TEXT=true``. O padrão é apenas
  ``sha256`` + comprimento, o que preserva a capacidade de agrupar/repetir
  chamadas sem armazenar dado pessoal identificável.
- **Retenção proporcional (LGPD, art. 15/16):** ``AUDIT_RETENTION_DAYS``
  controla o tempo máximo de guarda. A poda roda em background durante o
  ``lifespan`` e apaga eventos mais antigos que a janela.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete

from app.config import get_settings
from app.database import get_session_factory
from app.errors import AuditUnavailableError
from app.models import AuditEvent

logger = logging.getLogger(__name__)


def _normalize_query_text(query_text: str | None) -> str | None:
    """Aplica minimização quando exigida pelas configurações."""
    if query_text is None:
        return None
    if get_settings().audit_persist_query_text:
        return query_text
    digest = hashlib.sha256(query_text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}:len={len(query_text)}"


class AuditWriter:
    """Abre uma transação curta e independente para cada evento."""

    async def record(
        self,
        *,
        request_id: str,
        tenant_id: str,
        user_id: str,
        action: str,
        status: str,
        query_text: str | None = None,
        retrieved_sources: list[dict[str, Any]] | None = None,
        cited_sources: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
        duration_ms: int = 0,
        ip_address: str | None = None,
    ) -> None:
        try:
            async with get_session_factory()() as session:
                session.add(
                    AuditEvent(
                        request_id=request_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        action=action,
                        status=status,
                        query_text=_normalize_query_text(query_text),
                        retrieved_sources=retrieved_sources or [],
                        cited_sources=cited_sources or [],
                        details=details or {},
                        duration_ms=duration_ms,
                        ip_address=ip_address,
                    )
                )
                await session.commit()
        except Exception:
            # A indisponibilidade da auditoria não pode vazar detalhes do banco,
            # mas precisa ficar visível operacionalmente.
            logger.exception("audit_write_failed", extra={"action": action})
            raise AuditUnavailableError() from None

    async def prune_expired(self, *, retention_days: int) -> int:
        """Remove eventos mais antigos que ``retention_days``.

        Devolve quantas linhas foram apagadas para instrumentação.
        Retorno ``0`` também para o caso "retenção infinita" (dias = 0).
        """
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        try:
            async with get_session_factory()() as session:
                result = await session.execute(
                    delete(AuditEvent).where(AuditEvent.created_at < cutoff)
                )
                await session.commit()
                removed = int(result.rowcount or 0)
                if removed:
                    logger.info(
                        "audit_pruned",
                        extra={"removed": removed, "retention_days": retention_days},
                    )
                return removed
        except Exception:
            logger.exception("audit_prune_failed")
            return 0


audit_writer = AuditWriter()


def get_audit_writer() -> AuditWriter:
    return audit_writer


async def audit_prune_loop(*, retention_days: int, interval_hours: int) -> None:
    """Loop cooperativo: dorme ``interval_hours`` e chama ``prune_expired``.

    Vive dentro do ``lifespan``. Ao sair do contexto, o ``asyncio.CancelledError``
    encerra o loop de forma limpa.
    """
    if retention_days <= 0 or interval_hours <= 0:
        return
    interval_seconds = interval_hours * 3600
    while True:
        try:
            await audit_writer.prune_expired(retention_days=retention_days)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("audit_prune_loop_iteration_failed")
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
