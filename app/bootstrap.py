"""Bootstrap idempotente da primeira organização e do primeiro administrador."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.auth import hash_password
from app.config import get_settings
from app.database import get_session_factory
from app.models import Tenant, User

logger = logging.getLogger(__name__)


async def bootstrap_admin() -> None:
    settings = get_settings()
    if not settings.bootstrap_admin_password:
        logger.info("bootstrap_admin_skipped")
        return

    async with get_session_factory()() as session:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.slug == settings.bootstrap_tenant_slug)
        )
        if tenant is None:
            tenant = Tenant(
                slug=settings.bootstrap_tenant_slug,
                name=settings.bootstrap_tenant_name,
            )
            session.add(tenant)
            await session.flush()

        user = await session.scalar(
            select(User).where(
                User.tenant_id == tenant.id,
                User.username == settings.bootstrap_admin_username,
            )
        )
        if user is None:
            session.add(
                User(
                    tenant_id=tenant.id,
                    username=settings.bootstrap_admin_username,
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    role="admin",
                )
            )
            await session.commit()
            logger.info(
                "bootstrap_admin_created",
                extra={"tenant_slug": tenant.slug, "username": settings.bootstrap_admin_username},
            )

