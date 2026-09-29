"""Autenticação JWT, identidade e autorização por scopes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, Security, status
from fastapi.security import OAuth2PasswordBearer, SecurityScopes
from jwt import InvalidTokenError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db_session
from app.models import RefreshToken, Tenant, User

SCOPE_DESCRIPTIONS = {
    "rag:query": "Consultar a base de conhecimento",
    "documents:read": "Listar documentos do tenant",
    "documents:write": "Enviar e indexar documentos",
    "documents:validate": "Confirmar a situacao regulatoria dos documentos",
    "documents:delete": "Excluir documentos",
    "status:read": "Consultar detalhes operacionais",
    "audit:read": "Consultar a trilha de auditoria",
    "admin:manage": "Administrar usuários e organização",
}

ROLE_SCOPES = {
    "admin": frozenset(SCOPE_DESCRIPTIONS),
    "user": frozenset({"rag:query", "documents:read"}),
}

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/auth/token",
    scopes=SCOPE_DESCRIPTIONS,
    auto_error=False,
)
password_hasher = PasswordHasher()

# Hash descartável usado para gastar o mesmo tempo de Argon2 quando o usuário
# não existe. Sem isso o tempo de resposta denuncia quais logins são válidos.
_DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    tenant_id: str
    username: str
    role: str
    scopes: frozenset[str]


def build_principal(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        role=user.role,
        scopes=ROLE_SCOPES.get(user.role, frozenset()),
    )


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def burn_password_time() -> None:
    """Consome o custo de uma verificação Argon2 sem validar ninguém."""
    verify_password("senha-inexistente", _DUMMY_PASSWORD_HASH)


def create_access_token(user: User) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.jwt_access_token_minutes)
    scopes = sorted(ROLE_SCOPES.get(user.role, frozenset()))
    payload = {
        "sub": user.id,
        "tenant_id": user.tenant_id,
        "username": user.username,
        "role": user.role,
        "scopes": scopes,
        "ver": user.token_version or 0,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": expires,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, settings.jwt_access_token_minutes * 60


# ---------------------------------------------------------------------------
# Refresh tokens rotativos
# ---------------------------------------------------------------------------


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def issue_refresh_token(
    session: AsyncSession,
    user: User,
    *,
    family_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, RefreshToken]:
    """Cria um refresh token novo. O valor em claro só existe nesta resposta."""
    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    record = RefreshToken(
        family_id=family_id or str(uuid.uuid4()),
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=_digest(raw_token),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.refresh_token_days),
        user_agent=(user_agent or "")[:255] or None,
        ip_address=(ip_address or "")[:64] or None,
    )
    session.add(record)
    await session.flush()
    return raw_token, record


async def revoke_refresh_family(session: AsyncSession, family_id: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


async def find_refresh_token(
    session: AsyncSession, raw_token: str
) -> RefreshToken | None:
    return await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _digest(raw_token))
    )


async def bump_token_version(session: AsyncSession, user: User) -> None:
    """Invalida imediatamente todo access token já emitido para o usuário."""
    user.token_version = (user.token_version or 0) + 1
    await session.flush()


# ---------------------------------------------------------------------------
# Cookies de sessão e CSRF
# ---------------------------------------------------------------------------


def set_session_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh_token,
        max_age=settings.refresh_token_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/auth",
    )
    # Legível por JavaScript de propósito: é a metade do double-submit que o
    # front devolve no cabeçalho. Não dá acesso a nada sozinho.
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=settings.refresh_token_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.refresh_cookie_name, path="/api/auth")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(request: Request) -> None:
    """Double-submit: o cabeçalho precisa repetir o cookie não-httpOnly.

    Um site de terceiros consegue forçar o navegador a enviar o cookie, mas não
    consegue ler o valor para montar o cabeçalho.
    """
    settings = get_settings()
    cookie_value = request.cookies.get(settings.csrf_cookie_name) or ""
    header_value = request.headers.get(settings.csrf_header_name) or ""
    if not cookie_value or not hmac.compare_digest(cookie_value, header_value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token anti-CSRF ausente ou inválido.",
        )


def _credentials_error(required_scopes: list[str] | None = None) -> HTTPException:
    authenticate = "Bearer"
    if required_scopes:
        authenticate += f' scope="{" ".join(required_scopes)}"'
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas ou expiradas.",
        headers={"WWW-Authenticate": authenticate},
    )


async def get_current_principal(
    security_scopes: SecurityScopes,
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> Principal:
    settings = get_settings()
    if not token:
        raise _credentials_error(security_scopes.scopes)
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        user_id = str(payload["sub"])
        token_tenant_id = str(payload["tenant_id"])
        token_version = int(payload.get("ver", 0))
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise _credentials_error(security_scopes.scopes) from exc

    user = await session.scalar(
        select(User)
        .join(Tenant, Tenant.id == User.tenant_id)
        .where(User.id == user_id, Tenant.active.is_(True))
    )
    if user is None or not user.active or user.tenant_id != token_tenant_id:
        raise _credentials_error(security_scopes.scopes)
    # Logout global, troca de senha ou bloqueio derrubam o token na hora.
    if token_version != (user.token_version or 0) or user.is_locked():
        raise _credentials_error(security_scopes.scopes)

    scopes = ROLE_SCOPES.get(user.role, frozenset())
    missing = [scope for scope in security_scopes.scopes if scope not in scopes]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permissão insuficiente para esta operação.",
        )

    principal = Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        role=user.role,
        scopes=scopes,
    )
    request.state.principal = principal
    return principal


def require_scopes(*scopes: str):
    """Dependência declarativa usada pelas rotas protegidas."""
    return Security(get_current_principal, scopes=list(scopes))
