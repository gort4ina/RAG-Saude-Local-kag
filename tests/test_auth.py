"""Testes dos invariantes criptográficos e das claims de identidade."""

from __future__ import annotations

import jwt

from app.auth import create_access_token, hash_password, verify_password
from app.config import get_settings
from app.models import User


def test_password_is_argon2_hashed_and_verified() -> None:
    encoded = hash_password("uma-senha-forte")
    assert encoded.startswith("$argon2")
    assert verify_password("uma-senha-forte", encoded)
    assert not verify_password("senha-errada", encoded)


def test_access_token_carries_tenant_identity_and_scopes() -> None:
    user = User(
        id="user-1",
        tenant_id="tenant-1",
        username="admin",
        password_hash="unused",
        role="admin",
    )
    token, expires_in = create_access_token(user)
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    assert payload["sub"] == "user-1"
    assert payload["tenant_id"] == "tenant-1"
    assert "documents:delete" in payload["scopes"]
    assert payload["jti"]
    assert expires_in > 0
