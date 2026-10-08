"""Row-Level Security: cinto de seguranca no Postgres.

Em SQLite (testes) e no-op. Em PostgreSQL, ``SET LOCAL`` no inicio da
transacao faz as policies ``tenant_isolation`` filtrarem as linhas. Sem
``app.tenant_id`` e sem bypass, a policy nega o acesso — um SELECT
esquecido nao vaza entre tenants.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _is_postgres(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


async def apply_tenant_rls(
    session: AsyncSession,
    tenant_id: str | None,
    *,
    bypass: bool = False,
) -> None:
    """Aplica o GUC da transacao. Sem efeito fora do PostgreSQL."""
    if not _is_postgres(session):
        return
    if bypass:
        await session.execute(text("SELECT set_config('app.rls_bypass', '1', true)"))
    if tenant_id:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": tenant_id},
        )
