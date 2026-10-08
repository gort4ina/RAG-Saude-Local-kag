"""Tenant da requisicao atual, visivel para a sessao SQL (RLS).

O ``get_db_session`` e criado antes da autenticacao em algumas rotas.
O principal, depois de resolvido, grava o ``tenant_id`` aqui; a sessao
do Postgres aplica ``SET LOCAL app.tenant_id`` quando o dialeto permite.
"""

from __future__ import annotations

from contextvars import ContextVar

_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def get_tenant_id() -> str | None:
    return _tenant_id.get()


def set_tenant_id(tenant_id: str | None) -> None:
    _tenant_id.set(tenant_id)
