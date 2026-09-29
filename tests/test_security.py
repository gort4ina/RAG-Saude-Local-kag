"""Testes das defesas HTTP: sessão, lockout, CSRF, cabeçalhos e limites.

Diferente de ``test_api.py``, aqui o ``get_current_principal`` NÃO é
substituído: o objetivo é exercitar o fluxo real de autenticação contra um
banco SQLite temporário.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

TENANT_SLUG = "acme"
ADMIN_USERNAME = "gestora"
ADMIN_PASSWORD = "Regulatorio#Forte92"
USER_USERNAME = "analista"
USER_PASSWORD = "Analise#Segura47"


def _run(coroutine) -> Any:  # noqa: ANN001
    return asyncio.run(coroutine)


def _reset_database() -> tuple[str, str]:
    """Recria o schema e semeia um admin e um usuário comum."""
    from app.auth import hash_password
    from app.database import Base
    from app.models import Tenant, User

    async def _setup() -> tuple[str, str]:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            tenant = Tenant(slug=TENANT_SLUG, name="Acme Farma")
            session.add(tenant)
            await session.flush()
            admin = User(
                tenant_id=tenant.id,
                username=ADMIN_USERNAME,
                password_hash=hash_password(ADMIN_PASSWORD),
                role="admin",
            )
            member = User(
                tenant_id=tenant.id,
                username=USER_USERNAME,
                password_hash=hash_password(USER_PASSWORD),
                role="user",
            )
            session.add_all([admin, member])
            await session.commit()
            identifiers = (admin.id, member.id)
        await engine.dispose()
        return identifiers

    return _run(_setup())


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("UPLOAD_PATH", str(tmp_path / "uploads"))

    from app.config import get_settings
    from app.database import get_engine
    from app.dependencies import get_rag_service
    from app.main import app

    get_settings.cache_clear()
    get_rag_service.cache_clear()
    _reset_database()
    # O engine precisa nascer dentro do loop do TestClient; um pool herdado de
    # outro loop de evento quebra o aiosqlite.
    get_engine.cache_clear()

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    get_rag_service.cache_clear()
    get_engine.cache_clear()


def _login(
    client: TestClient, username: str = ADMIN_USERNAME, password: str = ADMIN_PASSWORD
):
    return client.post(
        "/api/auth/token",
        data={"username": f"{TENANT_SLUG}/{username}", "password": password},
    )


def _csrf_headers(client: TestClient) -> dict[str, str]:
    from app.config import get_settings

    settings = get_settings()
    return {settings.csrf_header_name: client.cookies.get(settings.csrf_cookie_name, "")}


# ---------------------------------------------------------------------------
# Cabeçalhos de defesa
# ---------------------------------------------------------------------------


def test_security_headers_are_present_on_every_response(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"


def test_security_headers_are_present_on_error_responses(client: TestClient) -> None:
    response = client.get("/api/documents")
    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_issues_access_and_refresh_cookies(client: TestClient) -> None:
    from app.config import get_settings

    settings = get_settings()
    response = _login(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert client.cookies.get(settings.refresh_cookie_name)
    assert client.cookies.get(settings.csrf_cookie_name)


def test_refresh_cookie_is_httponly_and_scoped_to_auth(client: TestClient) -> None:
    from app.config import get_settings

    settings = get_settings()
    raw = _login(client).headers.get_list("set-cookie")
    refresh_cookie = next(
        item for item in raw if item.startswith(settings.refresh_cookie_name)
    )
    assert "HttpOnly" in refresh_cookie
    assert "SameSite=strict" in refresh_cookie.replace("samesite", "SameSite")
    assert "Path=/api/auth" in refresh_cookie


def test_unknown_user_and_wrong_password_are_indistinguishable(
    client: TestClient,
) -> None:
    ghost = _login(client, username="nao-existe", password=ADMIN_PASSWORD)
    wrong = _login(client, password="SenhaErrada#123456")
    assert ghost.status_code == wrong.status_code == 401
    assert ghost.json()["detail"] == wrong.json()["detail"]


def test_account_locks_after_repeated_failures(client: TestClient) -> None:
    from app.config import get_settings

    attempts = get_settings().login_max_failed_attempts
    for _ in range(attempts):
        assert _login(client, password="SenhaErrada#123456").status_code == 401

    # A senha correta também é recusada enquanto o bloqueio estiver ativo.
    assert _login(client).status_code == 401


def test_login_counter_resets_after_success(client: TestClient) -> None:
    from app.config import get_settings

    attempts = get_settings().login_max_failed_attempts
    for _ in range(attempts - 1):
        _login(client, password="SenhaErrada#123456")
    assert _login(client).status_code == 200

    for _ in range(attempts - 1):
        _login(client, password="SenhaErrada#123456")
    assert _login(client).status_code == 200


# ---------------------------------------------------------------------------
# Refresh rotativo e CSRF
# ---------------------------------------------------------------------------


def test_refresh_requires_csrf_header(client: TestClient) -> None:
    _login(client)
    assert client.post("/api/auth/refresh").status_code == 403


def test_refresh_rotates_the_token_and_returns_a_new_access_token(
    client: TestClient,
) -> None:
    from app.config import get_settings

    settings = get_settings()
    _login(client)
    first_refresh = client.cookies.get(settings.refresh_cookie_name)

    response = client.post("/api/auth/refresh", headers=_csrf_headers(client))
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]
    assert client.cookies.get(settings.refresh_cookie_name) != first_refresh


def test_replaying_a_rotated_refresh_token_kills_the_whole_family(
    client: TestClient,
) -> None:
    from app.config import get_settings

    settings = get_settings()
    _login(client)
    stolen = client.cookies.get(settings.refresh_cookie_name)
    csrf = client.cookies.get(settings.csrf_cookie_name)

    assert client.post("/api/auth/refresh", headers=_csrf_headers(client)).status_code == 200
    live_refresh = client.cookies.get(settings.refresh_cookie_name)

    # O token antigo volta a aparecer: sinal de cópia.
    replay = client.post(
        "/api/auth/refresh",
        headers={settings.csrf_header_name: csrf},
        cookies={settings.refresh_cookie_name: stolen, settings.csrf_cookie_name: csrf},
    )
    assert replay.status_code == 401

    # O token legítimo também deixa de valer: a família inteira foi revogada.
    reuse = client.post(
        "/api/auth/refresh",
        headers={settings.csrf_header_name: csrf},
        cookies={
            settings.refresh_cookie_name: live_refresh,
            settings.csrf_cookie_name: csrf,
        },
    )
    assert reuse.status_code == 401


def test_logout_revokes_the_refresh_token(client: TestClient) -> None:
    from app.config import get_settings

    settings = get_settings()
    _login(client)
    refresh = client.cookies.get(settings.refresh_cookie_name)
    csrf = client.cookies.get(settings.csrf_cookie_name)

    assert client.post("/api/auth/logout", headers=_csrf_headers(client)).status_code == 200

    retry = client.post(
        "/api/auth/refresh",
        headers={settings.csrf_header_name: csrf},
        cookies={settings.refresh_cookie_name: refresh, settings.csrf_cookie_name: csrf},
    )
    assert retry.status_code == 401


# ---------------------------------------------------------------------------
# Autorização por scope
# ---------------------------------------------------------------------------


def test_regular_user_cannot_reach_admin_routes(client: TestClient) -> None:
    token = _login(client, USER_USERNAME, USER_PASSWORD).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.get("/api/admin/users", headers=headers).status_code == 403
    assert client.get("/api/audit", headers=headers).status_code == 403


def test_profile_exposes_only_the_scopes_of_the_role(client: TestClient) -> None:
    token = _login(client, USER_USERNAME, USER_PASSWORD).json()["access_token"]
    body = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert set(body["scopes"]) == {"rag:query", "documents:read"}
    assert body["session_idle_minutes"] > 0


def test_tampered_token_is_rejected(client: TestClient) -> None:
    token = _login(client).json()["access_token"]
    forged = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    response = client.get(
        "/api/documents", headers={"Authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401


def test_deactivating_a_user_invalidates_the_live_access_token(
    client: TestClient,
) -> None:
    member_token = _login(client, USER_USERNAME, USER_PASSWORD).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}
    assert client.get("/api/auth/me", headers=member_headers).status_code == 200

    admin_token = _login(client).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    users = client.get("/api/admin/users", headers=admin_headers).json()
    member_id = next(item["user_id"] for item in users if item["username"] == USER_USERNAME)

    disable = client.patch(
        f"/api/admin/users/{member_id}", json={"active": False}, headers=admin_headers
    )
    assert disable.status_code == 200, disable.text
    assert client.get("/api/auth/me", headers=member_headers).status_code == 401


def test_admin_cannot_deactivate_itself(client: TestClient) -> None:
    token = _login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    profile = client.get("/api/auth/me", headers=headers).json()
    response = client.patch(
        f"/api/admin/users/{profile['user_id']}", json={"active": False}, headers=headers
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Política de senha
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        "curta#A1",
        "senhasenhasenha",
        "tudominusculo#123",
        "SEMNUMERO#ABCDEF",
        "SemSimbolo123456",
        "Senha#Muito#Forte1",
        "aaaaAAAA1111####",
    ],
)
def test_weak_passwords_are_refused(client: TestClient, password: str) -> None:
    token = _login(client).json()["access_token"]
    response = client.post(
        "/api/admin/users",
        json={"username": "novato", "password": password, "role": "user"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422, password


def test_password_cannot_contain_the_username(client: TestClient) -> None:
    token = _login(client).json()["access_token"]
    response = client.post(
        "/api/admin/users",
        json={"username": "joana", "password": "Joana#Absoluta9", "role": "user"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_strong_password_creates_the_user_and_allows_login(client: TestClient) -> None:
    token = _login(client).json()["access_token"]
    created = client.post(
        "/api/admin/users",
        json={"username": "novato", "password": "Trilha#Vetor2026", "role": "user"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    assert _login(client, "novato", "Trilha#Vetor2026").status_code == 200


def test_password_change_terminates_existing_sessions(client: TestClient) -> None:
    token = _login(client, USER_USERNAME, USER_PASSWORD).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/api/auth/password",
        json={"current_password": USER_PASSWORD, "new_password": "Outra#Chave2026"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert _login(client, USER_USERNAME, "Outra#Chave2026").status_code == 200


# ---------------------------------------------------------------------------
# Limite de corpo
# ---------------------------------------------------------------------------


def test_oversized_json_body_is_rejected_before_handling(client: TestClient) -> None:
    from app.config import get_settings

    token = _login(client).json()["access_token"]
    oversized = "a" * (get_settings().max_json_body_bytes + 2048)
    response = client.post(
        "/api/chat",
        json={"question": oversized},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"
