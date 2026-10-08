"""Conexao assíncrona com o banco relacional."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Base declarativa compartilhada pelos modelos SQLAlchemy."""


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    from app.services.rls import apply_tenant_rls
    from app.tenant_context import get_tenant_id

    async with get_session_factory()() as session:
        await apply_tenant_rls(session, get_tenant_id())
        yield session


async def dispose_database() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()

