"""API HTTP da RAG local."""

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.extension import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    Principal,
    ROLE_SCOPES,
    bump_token_version,
    burn_password_time,
    clear_session_cookies,
    create_access_token,
    find_refresh_token,
    hash_password,
    issue_refresh_token,
    new_csrf_token,
    require_scopes,
    revoke_refresh_family,
    set_session_cookies,
    verify_csrf,
    verify_password,
)
from app.bootstrap import bootstrap_admin
from app.config import get_settings
from app.database import dispose_database, get_db_session
from app.dependencies import get_knowledge_graph_service, get_rag_service
from app.errors import (
    DocumentTooLargeError,
    EmptyDocumentError,
    RagError,
    UnsupportedDocumentError,
)
from app.logging_config import configure_logging, get_request_id, new_request_id, set_request_id
from app.schemas import (
    AuditEventResponse,
    ChangePasswordRequest,
    ChatRequest,
    ChatResponse,
    ComponentStatus,
    CreateUserRequest,
    DocumentSummary,
    DocumentStatusResponse,
    DocumentStatusUpdate,
    DeleteResponse,
    ErrorResponse,
    HealthResponse,
    KagRelationView,
    KagValidationUpdate,
    KnowledgeBaseStatus,
    LoadedModel,
    LogoutResponse,
    ReadyResponse,
    SystemStatus,
    TokenResponse,
    UploadResponse,
    UserProfile,
    UserStatusUpdate,
    UserSummary,
    validate_password_strength,
)
from app.models import AuditEvent, RefreshToken, Tenant, User
from app.security import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
    client_ip,
)
from app.services.audit import AuditWriter, audit_prune_loop, get_audit_writer
from app.services.graph_store import GraphStore
from app.services.document_loader import (
    document_hash,
    validate_content_signature,
    validate_extension,
)
from app.services.ollama_client import model_matches
from app.services.rag_service import RagService
from app.services.inference_gate import InferenceGate, get_inference_gate
from app.services.metadata import STATUS_UNVERIFIED, status_label
from app.services import metrics as prom_metrics

settings = get_settings()
settings.ensure_directories()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    return client_ip(request)


def _rate_key(request: Request) -> str:
    """Conta por identidade quando ela já foi resolvida, senão por IP.

    Os limites por rota rodam depois das dependências, então lá o principal já
    existe e um usuário não consome a cota do vizinho atrás do mesmo NAT. O
    limite global roda antes disso e sempre cai no IP.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return f"{principal.tenant_id}:{principal.user_id}"
    return client_ip(request)


limiter = Limiter(
    key_func=_rate_key,
    default_limits=[settings.default_rate_limit],
    storage_uri=settings.rate_limit_storage_uri,
)


def _resolve_service(application: FastAPI) -> RagService:
    """Resolve o servico respeitando overrides de teste."""
    factory = application.dependency_overrides.get(get_rag_service, get_rag_service)
    return factory()


def _assert_single_worker_safe() -> None:
    """Em producao, recusa subir se houver mais de 1 worker com estado local.

    O backend tem tres componentes process-local hoje:
    - Chroma embedded (``chroma_path``) com SQLite proprio;
    - BM25 em memoria (``Bm25Index``);
    - rate limit via ``memory://`` do SlowAPI;
    - ``InferenceGate`` baseado em ``asyncio.Semaphore``.

    Rodar com ``uvicorn --workers > 1`` ou ``WEB_CONCURRENCY > 1`` *vai*
    corromper o SQLite do Chroma, fragmentar o BM25 e multiplicar o teto
    do rate limit por N. Em dev avisamos; em producao abortamos.
    """
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    except ValueError:
        workers = 1
    if workers <= 1:
        return

    issues: list[str] = []
    if settings.rate_limit_storage_uri.startswith("memory://"):
        issues.append(
            "RATE_LIMIT_STORAGE_URI=memory:// (defina um backend compartilhado, "
            "ex.: redis://redis:6379/0)"
        )
    # Chroma embedded sempre eh process-local. Enquanto nao houver
    # ``VECTOR_BACKEND=pgvector`` (plano em docs/PGVECTOR-PLANO.md), assumimos
    # Chroma e qualquer workers>1 eh invalido.
    issues.append(
        "Chroma embedded e BM25 em memoria sao process-local: use 1 worker ate "
        "concluir a migracao para pgvector (docs/PGVECTOR-PLANO.md)"
    )

    message = (
        f"WEB_CONCURRENCY={workers} com componentes process-local: "
        + " | ".join(issues)
    )
    if settings.is_production:
        raise RuntimeError(message)
    logger.warning("worker_configuration_unsafe", extra={"detail": message})


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Abre o pool HTTP do Ollama, reconstrói o BM25 e agenda a poda LGPD."""
    _assert_single_worker_safe()
    service = _resolve_service(application)
    startup = getattr(service.ollama, "startup", None)
    if callable(startup):
        await startup()
    warmup = getattr(service, "warmup", None)
    if callable(warmup):
        await warmup()
    await bootstrap_admin()

    # Poda de AuditEvent expirados: roda em background em vez de bloquear a
    # subida. Uma execução falha só emite log e a próxima iteração tenta de
    # novo — o loop não pode derrubar o app.
    prune_task: asyncio.Task | None = None
    if settings.audit_retention_days > 0:
        prune_task = asyncio.create_task(
            audit_prune_loop(
                retention_days=settings.audit_retention_days,
                interval_hours=settings.audit_prune_interval_hours,
            ),
            name="audit-prune-loop",
        )

    logger.info(
        "application_started",
        extra={
            "collection": settings.collection_name,
            "chat_model": settings.chat_model,
            "embedding_model": settings.embedding_model,
            "bm25_index_size": service.bm25.size,
            "audit_retention_days": settings.audit_retention_days,
        },
    )
    try:
        yield
    finally:
        if prune_task is not None:
            prune_task.cancel()
            try:
                await prune_task
            except (asyncio.CancelledError, Exception):
                pass
        shutdown = getattr(service.ollama, "shutdown", None)
        if callable(shutdown):
            await shutdown()
        await dispose_database()
        logger.info("application_stopped")


app = FastAPI(
    title=settings.app_name,
    version="2.1.0",
    description=(
        "RAG local para assuntos regulatorios farmaceuticos, "
        "sem dependencia de APIs pagas."
    ),
    lifespan=lifespan,
    # Fora de desenvolvimento o schema deixa de ser publicado: ele entrega o
    # mapa completo de rotas e scopes a quem ainda nao esta autenticado.
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url="/redoc" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


async def request_id_middleware(request: Request, call_next):
    """Propaga o X-Request-ID para todos os logs da requisicao.

    IDs recebidos no cabecalho tambem sao fixados no contexto; sem isso os
    logs sairiam com um id diferente do devolvido ao cliente.
    """
    incoming = (request.headers.get("x-request-id") or "").strip()
    if incoming:
        request_id = incoming[:64]
        set_request_id(request_id)
    else:
        request_id = new_request_id()

    logger.info(
        "http_request",
        extra={"method": request.method, "path": request.url.path},
    )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("http_unhandled", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code="internal_error",
                detail="Erro interno inesperado.",
                request_id=request_id,
            ).model_dump(),
            headers={"X-Request-ID": request_id},
        )
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "http_response",
        extra={"path": request.url.path, "status": response.status_code},
    )
    return response


# A pilha e montada de dentro para fora: o ultimo `add_middleware` e o primeiro
# a ver a requisicao. Ordem efetiva de entrada:
#   security headers -> CORS -> request id -> host -> tamanho do corpo -> limite
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(BodySizeLimitMiddleware, settings=settings)
if "*" not in settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.middleware("http")(request_id_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Request-ID",
        settings.csrf_header_name,
    ],
    expose_headers=["X-Request-ID", "Retry-After"],
    max_age=600,
)
app.add_middleware(SecurityHeadersMiddleware, settings=settings)


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------


INVALID_CREDENTIALS = HTTPException(
    status_code=401,
    detail="Usuário ou senha inválidos.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _session_rejected() -> JSONResponse:
    """401 que também apaga os cookies da sessão.

    Precisa ser um retorno, e não um ``raise``: os cabeçalhos escritos no
    ``Response`` injetado só entram na resposta quando o handler termina
    normalmente, então limpar cookies antes de levantar uma exceção não teria
    efeito nenhum.
    """
    response = JSONResponse(
        status_code=401,
        content=ErrorResponse(
            code="invalid_credentials",
            detail="Credenciais inválidas ou expiradas.",
            request_id=get_request_id(),
        ).model_dump(),
        headers={"WWW-Authenticate": "Bearer"},
    )
    clear_session_cookies(response)
    return response


def _profile_of(principal: Principal) -> UserProfile:
    return UserProfile(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        username=principal.username,
        role=principal.role,
        scopes=sorted(principal.scopes),
        session_idle_minutes=settings.session_idle_minutes,
    )


async def _start_session(
    request: Request,
    response: Response,
    session: AsyncSession,
    user: User,
) -> TokenResponse:
    """Emite o par access token + refresh token e planta os cookies."""
    access_token, expires_in = create_access_token(user)
    refresh_token, _ = await issue_refresh_token(
        session,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )
    set_session_cookies(response, refresh_token, new_csrf_token())
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@app.post("/api/auth/token", response_model=TokenResponse)
@limiter.limit(settings.login_rate_limit)
async def issue_token(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(OAuth2PasswordRequestForm),
    session: AsyncSession = Depends(get_db_session),
    audit: AuditWriter = Depends(get_audit_writer),
) -> TokenResponse:
    """Emite um access token. Use ``tenant/usuario`` ou apenas o usuário local.

    Toda falha devolve a mesma mensagem e gasta o mesmo tempo de Argon2, mesmo
    quando o usuário não existe ou está bloqueado: diferenciar as respostas
    entregaria uma lista de logins válidos a quem estivesse sondando.
    """
    raw_username = form.username.strip()
    if "/" in raw_username:
        tenant_slug, username = raw_username.split("/", 1)
    else:
        tenant_slug, username = settings.bootstrap_tenant_slug, raw_username

    ip_address = _client_ip(request)
    user = await session.scalar(
        select(User)
        .join(Tenant, Tenant.id == User.tenant_id)
        .where(
            Tenant.slug == tenant_slug,
            Tenant.active.is_(True),
            User.username == username,
            User.active.is_(True),
        )
    )
    if user is None:
        burn_password_time()
        await audit.record(
            request_id=get_request_id(),
            tenant_id=tenant_slug[:36] or "desconhecido",
            user_id="desconhecido",
            action="auth.login",
            status="unknown_user",
            details={"username": username[:120]},
            ip_address=ip_address,
        )
        raise INVALID_CREDENTIALS

    if user.is_locked():
        burn_password_time()
        await audit.record(
            request_id=get_request_id(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            action="auth.login",
            status="locked",
            ip_address=ip_address,
        )
        raise INVALID_CREDENTIALS

    if not verify_password(form.password, user.password_hash):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        locked = user.failed_login_attempts >= settings.login_max_failed_attempts
        if locked:
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=settings.login_lockout_minutes
            )
            user.failed_login_attempts = 0
        await session.commit()
        await audit.record(
            request_id=get_request_id(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            action="auth.login",
            status="locked_out" if locked else "bad_password",
            details={"lockout_minutes": settings.login_lockout_minutes} if locked else {},
            ip_address=ip_address,
        )
        raise INVALID_CREDENTIALS

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    token_response = await _start_session(request, response, session, user)
    await session.commit()
    await audit.record(
        request_id=get_request_id(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        action="auth.login",
        status="success",
        ip_address=ip_address,
    )
    return token_response


@app.post("/api/auth/refresh", response_model=TokenResponse)
@limiter.limit(settings.refresh_rate_limit)
async def refresh_session(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    audit: AuditWriter = Depends(get_audit_writer),
) -> TokenResponse | JSONResponse:
    """Troca o refresh token do cookie por um par novo.

    A rotação é obrigatória: cada refresh invalida o anterior. Se um token já
    rotacionado voltar a aparecer, ou ele foi copiado ou a cópia é a que está
    sendo usada agora — nos dois casos a família inteira cai.
    """
    verify_csrf(request)
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if not raw_token:
        return _session_rejected()

    record = await find_refresh_token(session, raw_token)
    if record is None:
        return _session_rejected()

    ip_address = _client_ip(request)
    if not record.is_active:
        await revoke_refresh_family(session, record.family_id)
        await session.commit()
        await audit.record(
            request_id=get_request_id(),
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            action="auth.refresh",
            status="reuse_detected",
            details={"family_id": record.family_id},
            ip_address=ip_address,
        )
        return _session_rejected()

    user = await session.scalar(
        select(User)
        .join(Tenant, Tenant.id == User.tenant_id)
        .where(User.id == record.user_id, Tenant.active.is_(True))
    )
    if user is None or not user.active or user.is_locked():
        await revoke_refresh_family(session, record.family_id)
        await session.commit()
        return _session_rejected()

    access_token, expires_in = create_access_token(user)
    rotated, new_record = await issue_refresh_token(
        session,
        user,
        family_id=record.family_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=ip_address,
    )
    record.revoked_at = datetime.now(timezone.utc)
    record.replaced_by = new_record.id
    await session.commit()
    set_session_cookies(response, rotated, new_csrf_token())
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@app.post("/api/auth/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> LogoutResponse:
    """Revoga a família de refresh tokens desta sessão e limpa os cookies.

    Não exige access token válido: quem já perdeu a sessão ainda precisa
    conseguir encerrar o que ficou no servidor.
    """
    verify_csrf(request)
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if raw_token:
        record = await find_refresh_token(session, raw_token)
        if record is not None:
            await revoke_refresh_family(session, record.family_id)
            await session.commit()
    clear_session_cookies(response)
    return LogoutResponse()


@app.post("/api/auth/logout-all", response_model=LogoutResponse)
async def logout_all_devices(
    request: Request,
    response: Response,
    principal: Principal = require_scopes(),
    session: AsyncSession = Depends(get_db_session),
    audit: AuditWriter = Depends(get_audit_writer),
) -> LogoutResponse:
    """Derruba todas as sessões do usuário, em qualquer dispositivo."""
    user = await session.get(User, principal.user_id)
    if user is None:
        raise INVALID_CREDENTIALS
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await bump_token_version(session, user)
    await session.commit()
    clear_session_cookies(response)
    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="auth.logout_all",
        status="success",
        ip_address=_client_ip(request),
    )
    return LogoutResponse()


@app.get("/api/auth/me", response_model=UserProfile)
async def current_user(
    principal: Principal = require_scopes(),
) -> UserProfile:
    return _profile_of(principal)


@app.post("/api/auth/password", response_model=LogoutResponse)
@limiter.limit(settings.login_rate_limit)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    principal: Principal = require_scopes(),
    session: AsyncSession = Depends(get_db_session),
    audit: AuditWriter = Depends(get_audit_writer),
) -> LogoutResponse:
    """Troca a própria senha e encerra todas as sessões abertas."""
    user = await session.get(User, principal.user_id)
    if user is None or not verify_password(payload.current_password, user.password_hash):
        burn_password_time()
        raise INVALID_CREDENTIALS
    try:
        validate_password_strength(payload.new_password, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await bump_token_version(session, user)
    await session.commit()
    clear_session_cookies(response)
    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="auth.password_change",
        status="success",
        ip_address=_client_ip(request),
    )
    return LogoutResponse()


# ---------------------------------------------------------------------------
# Administração de usuários
# ---------------------------------------------------------------------------


@app.get("/api/admin/users", response_model=list[UserSummary])
async def list_users(
    principal: Principal = require_scopes("admin:manage"),
    session: AsyncSession = Depends(get_db_session),
) -> list[UserSummary]:
    users = (
        await session.scalars(
            select(User)
            .where(User.tenant_id == principal.tenant_id)
            .order_by(User.username)
        )
    ).all()
    return [
        UserSummary(
            user_id=user.id,
            username=user.username,
            role=user.role,
            active=user.active,
            locked=user.is_locked(),
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )
        for user in users
    ]


@app.post("/api/admin/users", response_model=UserProfile, status_code=201)
@limiter.limit(settings.admin_rate_limit)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    principal: Principal = require_scopes("admin:manage"),
    session: AsyncSession = Depends(get_db_session),
    audit: AuditWriter = Depends(get_audit_writer),
) -> UserProfile:
    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="user.create.requested",
        status="started",
        details={"username": payload.username, "role": payload.role},
        ip_address=_client_ip(request),
    )
    user = User(
        tenant_id=principal.tenant_id,
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Usuário já existe neste tenant.") from exc
    await session.refresh(user)
    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="user.create",
        status="success",
        details={
            "created_user_id": user.id,
            "username": user.username,
            "role": user.role,
        },
        ip_address=_client_ip(request),
    )
    return UserProfile(
        user_id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        role=user.role,
        scopes=sorted(ROLE_SCOPES.get(user.role, frozenset())),
        session_idle_minutes=settings.session_idle_minutes,
    )


@app.patch("/api/admin/users/{user_id}", response_model=UserSummary)
@limiter.limit(settings.admin_rate_limit)
async def set_user_active(
    user_id: str,
    payload: UserStatusUpdate,
    request: Request,
    principal: Principal = require_scopes("admin:manage"),
    session: AsyncSession = Depends(get_db_session),
    audit: AuditWriter = Depends(get_audit_writer),
) -> UserSummary:
    """Ativa ou desativa um usuário do próprio tenant.

    Desativar derruba as sessões na hora: os refresh tokens são revogados e o
    ``token_version`` sobe, invalidando os access tokens ainda dentro da
    validade.
    """
    if user_id == principal.user_id:
        raise HTTPException(
            status_code=400, detail="Um administrador não pode desativar a si mesmo."
        )
    user = await session.scalar(
        select(User).where(User.id == user_id, User.tenant_id == principal.tenant_id)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    user.active = payload.active
    if not payload.active:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await bump_token_version(session, user)
    else:
        user.locked_until = None
        user.failed_login_attempts = 0
    await session.commit()
    await session.refresh(user)
    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="user.activate" if payload.active else "user.deactivate",
        status="success",
        details={"target_user_id": user.id, "username": user.username},
        ip_address=_client_ip(request),
    )
    return UserSummary(
        user_id=user.id,
        username=user.username,
        role=user.role,
        active=user.active,
        locked=user.is_locked(),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@app.exception_handler(RagError)
async def rag_error_handler(_: Request, exc: RagError) -> JSONResponse:
    logger.warning(
        "rag_error",
        extra={"code": exc.code, "http_status": exc.http_status},
        exc_info=exc,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(
            code=exc.code,
            detail=exc.user_message,
            request_id=get_request_id(),
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    logger.info("validation_error", extra={"error_count": len(exc.errors())})
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            code="invalid_request",
            detail="A requisicao possui campos invalidos.",
            request_id=get_request_id(),
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Observabilidade
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse)
@limiter.exempt
async def health(service: RagService = Depends(get_rag_service)) -> HealthResponse:
    """Disponibilidade basica da API e do Ollama."""
    ollama_state = "online" if await service.ollama.health() else "offline"
    return HealthResponse(
        status="ok" if ollama_state == "online" else "degraded",
        ollama=ollama_state,
        chat_model=settings.chat_model,
        embedding_model=settings.embedding_model,
    )


@app.get("/api/ready", response_model=ReadyResponse)
async def ready(
    service: RagService = Depends(get_rag_service),
    session: AsyncSession = Depends(get_db_session),
    _: Principal = require_scopes("status:read"),
) -> ReadyResponse:
    """True apenas quando todos os componentes essenciais respondem."""
    reasons: list[str] = []

    if not service.store.health():
        reasons.append("Banco vetorial nao responde.")
    try:
        await session.execute(select(1))
    except Exception:
        reasons.append("Banco relacional nao responde.")

    installed = await service.ollama.list_installed_models()
    if not installed:
        reasons.append("Ollama nao respondeu ou nao possui modelos instalados.")
    else:
        if not any(model_matches(name, settings.chat_model) for name in installed):
            reasons.append(f"Modelo de chat ausente: {settings.chat_model}.")
        if not any(model_matches(name, settings.embedding_model) for name in installed):
            reasons.append(f"Modelo de embeddings ausente: {settings.embedding_model}.")

    return ReadyResponse(ready=not reasons, reasons=reasons)


@app.get("/api/status", response_model=SystemStatus)
async def system_status(
    service: RagService = Depends(get_rag_service),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = require_scopes("status:read"),
) -> SystemStatus:
    """Estado consolidado de todos os componentes, incluindo uso de GPU."""
    store_ok = service.store.health()
    installed = await service.ollama.list_installed_models()
    chat_ok = any(model_matches(name, settings.chat_model) for name in installed)
    embed_ok = any(model_matches(name, settings.embedding_model) for name in installed)
    try:
        await session.execute(select(1))
        relational_ok = True
    except Exception:
        relational_ok = False

    loaded: list[LoadedModel] = []
    for entry in await service.ollama.loaded_models():
        size = int(entry.get("size") or 0)
        size_vram = int(entry.get("size_vram") or 0)
        model_name = str(entry.get("model") or entry.get("name") or "")
        loaded.append(
            LoadedModel(
                name=model_name,
                size_mb=size // (1024 * 1024),
                size_vram_mb=size_vram // (1024 * 1024),
                on_gpu=size_vram > 0,
            )
        )
        if model_name:
            prom_metrics.ollama_model_size_bytes.labels(model=model_name).set(size)
            prom_metrics.ollama_model_vram_bytes.labels(model=model_name).set(size_vram)

    gpu_in_use = any(item.on_gpu for item in loaded)
    if not loaded:
        gpu_detail = "Nenhum modelo residente. Faca uma pergunta e consulte novamente."
    elif gpu_in_use:
        partial = [item for item in loaded if 0 < item.size_vram_mb < item.size_mb]
        gpu_detail = (
            "Carga parcial na GPU: parte das camadas ficou em CPU/RAM."
            if partial
            else "Modelo(s) residentes integralmente na VRAM."
        )
    else:
        gpu_detail = "Modelo(s) residentes apenas em CPU/RAM."

    docs = service.documents(principal.tenant_id)
    prom_metrics.knowledge_chunks_total.set(sum(item.chunks for item in docs))
    return SystemStatus(
        app_name=settings.app_name,
        app_env=settings.app_env,
        components=[
            ComponentStatus(name="backend", ok=True, detail="fastapi"),
            ComponentStatus(
                name="ollama",
                ok=bool(installed),
                detail=(
                    f"{len(installed)} modelo(s) instalado(s)"
                    if installed
                    else "nao respondeu"
                ),
            ),
            ComponentStatus(
                name="vector_store", ok=store_ok, detail=settings.collection_name
            ),
            ComponentStatus(
                name="relational_database",
                ok=relational_ok,
                detail="postgresql" if "postgresql" in settings.database_url else "sqlite",
            ),
            ComponentStatus(
                name="bm25",
                ok=service.bm25_size(principal.tenant_id) > 0
                or not settings.hybrid_search_enabled,
                detail=f"{service.bm25_size(principal.tenant_id)} trecho(s) indexado(s)",
            ),
        ],
        chat_model=settings.chat_model,
        embedding_model=settings.embedding_model,
        chat_model_installed=chat_ok,
        embedding_model_installed=embed_ok,
        collection_name=settings.collection_name,
        documents_count=len(docs),
        chunks_count=sum(item.chunks for item in docs),
        bm25_index_size=service.bm25_size(principal.tenant_id),
        hybrid_search_enabled=settings.hybrid_search_enabled,
        loaded_models=loaded,
        gpu_in_use=gpu_in_use,
        gpu_detail=gpu_detail,
    )


@app.get("/api/metrics", include_in_schema=False)
async def prometheus_metrics(
    _: Principal = require_scopes("status:read"),
) -> Response:
    """Endpoint Prometheus. Protegido: expõe telemetria sensível do processo."""
    body, content_type = prom_metrics.render()
    return Response(content=body, media_type=content_type)


@app.get("/api/knowledge-base/status", response_model=KnowledgeBaseStatus)
def knowledge_base_status(
    service: RagService = Depends(get_rag_service),
    principal: Principal = require_scopes("documents:read"),
) -> KnowledgeBaseStatus:
    docs = service.documents(principal.tenant_id)
    chunks = sum(item.chunks for item in docs)
    return KnowledgeBaseStatus(
        ready=bool(docs),
        collection_name=settings.collection_name,
        embedding_model=settings.embedding_model,
        documents_count=len(docs),
        chunks_count=chunks,
        bm25_index_size=service.bm25_size(principal.tenant_id),
        unverified_documents=service.unverified_documents(principal.tenant_id),
        reason="" if docs else "Nenhum documento indexado.",
    )


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------


@app.get("/api/documents", response_model=list[DocumentSummary])
def list_documents(
    service: RagService = Depends(get_rag_service),
    principal: Principal = require_scopes("documents:read"),
) -> list[DocumentSummary]:
    """Lista documentos presentes na base."""
    return service.documents(principal.tenant_id)


@app.post("/api/documents/upload", response_model=UploadResponse, status_code=201)
@limiter.limit(settings.upload_rate_limit)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    service: RagService = Depends(get_rag_service),
    principal: Principal = require_scopes("documents:write"),
    audit: AuditWriter = Depends(get_audit_writer),
) -> UploadResponse:
    """Recebe e indexa PDF, TXT ou Markdown."""
    filename = Path(file.filename or "documento").name
    try:
        extension = validate_extension(filename)
    except ValueError as exc:
        raise UnsupportedDocumentError() from exc

    limit = settings.max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if not content:
        raise EmptyDocumentError()
    if len(content) > limit:
        raise DocumentTooLargeError(settings.max_upload_mb)
    try:
        validate_content_signature(extension, content)
    except ValueError as exc:
        raise UnsupportedDocumentError() from exc

    # Cota agregada por tenant: soma dos bytes de todos os chunks já indexados
    # + o payload atual. Evita que um único tenant esgote o disco compartilhado.
    # Resolve por request para permitir ajuste em tempo de teste.
    quota_bytes = get_settings().tenant_upload_quota_bytes
    if quota_bytes > 0:
        existing_bytes = sum(
            len((chunk_text or "").encode("utf-8"))
            for _chunk_id, chunk_text in service.store.all_chunks(principal.tenant_id)
        )
        if existing_bytes + len(content) > quota_bytes:
            await audit.record(
                request_id=get_request_id(),
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="document.upload.rejected",
                status="quota_exceeded",
                details={
                    "filename": filename,
                    "size_bytes": len(content),
                    "existing_bytes": existing_bytes,
                    "quota_bytes": quota_bytes,
                },
                ip_address=_client_ip(request),
            )
            raise HTTPException(
                status_code=413,
                detail=(
                    "Cota do tenant excedida: "
                    f"{quota_bytes // (1024*1024)} MB no total permitidos."
                ),
            )

    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="document.upload.requested",
        status="started",
        details={"filename": filename, "size_bytes": len(content)},
        ip_address=_client_ip(request),
    )

    tenant_key = hashlib.sha256(principal.tenant_id.encode("utf-8")).hexdigest()[:12]
    target = settings.upload_path / (
        f"upload_{tenant_key}_{document_hash(content)}{extension}"
    )
    target.write_bytes(content)
    try:
        result = await service.ingest(
            target,
            filename,
            content,
            tenant_id=principal.tenant_id,
        )
        await audit.record(
            request_id=get_request_id(),
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="document.upload",
            status="success",
            details={
                "document_id": result.document_id,
                "filename": result.filename,
                "chunks": result.chunks,
            },
            ip_address=_client_ip(request),
        )
        return result
    except ValueError as exc:
        raise EmptyDocumentError() from exc
    finally:
        target.unlink(missing_ok=True)


@app.delete("/api/documents/{document_id}", response_model=DeleteResponse)
async def delete_document(
    document_id: str,
    request: Request,
    service: RagService = Depends(get_rag_service),
    principal: Principal = require_scopes("documents:delete"),
    audit: AuditWriter = Depends(get_audit_writer),
) -> DeleteResponse:
    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="document.delete.requested",
        status="started",
        details={"document_id": document_id},
        ip_address=_client_ip(request),
    )
    deleted = await service.delete_document(
        document_id, tenant_id=principal.tenant_id
    )
    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="document.delete",
        status="success" if deleted else "not_found",
        details={"document_id": document_id},
        ip_address=_client_ip(request),
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return DeleteResponse(document_id=document_id, deleted=True)


@app.patch(
    "/api/documents/{document_id}/status",
    response_model=DocumentStatusResponse,
)
async def update_document_status(
    document_id: str,
    payload: DocumentStatusUpdate,
    request: Request,
    service: RagService = Depends(get_rag_service),
    principal: Principal = require_scopes("documents:validate"),
    audit: AuditWriter = Depends(get_audit_writer),
) -> DocumentStatusResponse:
    """Confirma ou remove manualmente a situacao regulatoria do documento."""
    updated, validated_at = await service.update_document_status(
        document_id,
        payload.status,
        tenant_id=principal.tenant_id,
        validated_by=principal.username,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")

    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="document.status.update",
        status="success",
        details={
            "document_id": document_id,
            "regulatory_status": payload.status,
            "updated_chunks": updated,
            "validated_by": principal.username,
        },
        ip_address=_client_ip(request),
    )
    return DocumentStatusResponse(
        document_id=document_id,
        status=payload.status,
        status_label=status_label(payload.status),
        updated_chunks=updated,
        validated_by=(
            None if payload.status == STATUS_UNVERIFIED else principal.username
        ),
        validated_at=validated_at,
    )


# ---------------------------------------------------------------------------
# KAG (Knowledge Augmented Generation)
# ---------------------------------------------------------------------------


def _kag_view(relation) -> KagRelationView:
    return KagRelationView(
        id=relation.id,
        relation=relation.relation,
        subject=relation.subject,
        subject_kind=relation.subject_kind,
        object=relation.object,
        object_kind=relation.object_kind,
        source_reference=relation.source_reference,
        source_document_id=relation.source_document_id,
        validation_status=relation.validation_status,
        extracted_by=relation.extracted_by,
        confidence=relation.confidence,
        source_span=relation.source_span,
        validated_by=relation.validated_by,
        validated_at=relation.validated_at,
    )


@app.get("/api/knowledge/relations", response_model=list[KagRelationView])
async def list_kag_relations(
    status: str | None = None,
    limit: int = 200,
    principal: Principal = require_scopes("documents:read"),
    kag: GraphStore | None = Depends(get_knowledge_graph_service),
) -> list[KagRelationView]:
    """Lista as arestas do KAG do tenant, opcionalmente filtradas por status.

    Com ``KAG_ENABLED=false`` devolve lista vazia (RAG puro).
    """
    if kag is None:
        return []
    relations = await kag.list_relations(principal.tenant_id, status=status, limit=limit)
    return [_kag_view(relation) for relation in relations]


@app.patch(
    "/api/knowledge/relations/{relation_id}",
    response_model=KagRelationView,
)
async def validate_kag_relation(
    relation_id: str,
    payload: KagValidationUpdate,
    request: Request,
    principal: Principal = require_scopes("documents:validate"),
    kag: GraphStore | None = Depends(get_knowledge_graph_service),
    audit: AuditWriter = Depends(get_audit_writer),
) -> KagRelationView:
    """Registra a decisão humana sobre uma aresta do KAG.

    Rejeitar mantém a aresta no banco (para histórico auditável) e apenas
    troca ``validation_status`` para ``rejeitada`` — a aresta não é mais
    injetada no prompt.
    """
    if kag is None:
        raise HTTPException(
            status_code=404,
            detail="KAG desabilitado neste ambiente (KAG_ENABLED=false).",
        )
    updated = await kag.set_validation_status(
        principal.tenant_id,
        relation_id,
        status=payload.status,
        validated_by=principal.username,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Relação KAG não encontrada.")

    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="kag.relation.validate",
        status="success",
        details={
            "relation_id": relation_id,
            "new_status": payload.status,
            "extracted_by": updated.extracted_by,
        },
        ip_address=_client_ip(request),
    )
    return _kag_view(updated)


@app.get("/api/audit", response_model=list[AuditEventResponse])
async def list_audit_events(
    limit: int = 100,
    principal: Principal = require_scopes("audit:read"),
    session: AsyncSession = Depends(get_db_session),
) -> list[AuditEventResponse]:
    safe_limit = max(1, min(limit, 500))
    events = (
        await session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == principal.tenant_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(safe_limit)
        )
    ).all()
    return [
        AuditEventResponse(
            id=event.id,
            request_id=event.request_id,
            user_id=event.user_id,
            action=event.action,
            status=event.status,
            query_text=event.query_text,
            retrieved_sources=event.retrieved_sources,
            cited_sources=event.cited_sources,
            details=event.details,
            duration_ms=event.duration_ms,
            ip_address=event.ip_address,
            created_at=event.created_at,
        )
        for event in events
    ]


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@app.post("/api/chat", response_model=ChatResponse)
@limiter.limit(settings.chat_rate_limit)
async def chat(
    request: Request,
    payload: ChatRequest,
    service: RagService = Depends(get_rag_service),
    principal: Principal = require_scopes("rag:query"),
    audit: AuditWriter = Depends(get_audit_writer),
    gate: InferenceGate = Depends(get_inference_gate),
) -> ChatResponse:
    """Recuperacao hibrida e geracao fundamentada (resposta unica em JSON)."""
    question = payload.question.strip()
    await audit.record(
        request_id=get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="rag.query.requested",
        status="started",
        query_text=question,
        ip_address=_client_ip(request),
    )
    await gate.acquire()
    try:
        response = await service.answer(question, tenant_id=principal.tenant_id)
    finally:
        gate.release()
    await audit.record(
        request_id=response.request_id or get_request_id(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="rag.query",
        status="success",
        query_text=question,
        retrieved_sources=[item.model_dump(mode="json") for item in response.retrieved_sources],
        cited_sources=[item.model_dump(mode="json") for item in response.sources],
        details={
            "grounded": response.grounded,
            "requires_human_review": response.requires_human_review,
        },
        duration_ms=response.duration_ms,
        ip_address=_client_ip(request),
    )
    return response


@app.post("/api/chat/stream")
@limiter.limit(settings.chat_rate_limit)
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    service: RagService = Depends(get_rag_service),
    principal: Principal = require_scopes("rag:query"),
    audit: AuditWriter = Depends(get_audit_writer),
    gate: InferenceGate = Depends(get_inference_gate),
) -> StreamingResponse:
    """Mesma pipeline do /api/chat, emitindo NDJSON token a token.

    Tipos de evento: ``metadata``, ``token``, ``done``, ``error``.
    """
    question = payload.question.strip()
    request_id = get_request_id()
    await audit.record(
        request_id=request_id,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="rag.query.stream.requested",
        status="started",
        query_text=question,
        ip_address=_client_ip(request),
    )
    await gate.acquire()

    async def generate() -> AsyncIterator[str]:
        set_request_id(request_id)
        retrieved_sources: list[dict] = []
        cited_sources: list[dict] = []
        final_status = "success"
        duration_ms = 0
        try:
            async for event in service.answer_stream(
                question, tenant_id=principal.tenant_id
            ):
                if event.get("type") == "metadata":
                    retrieved_sources = list(event.get("sources") or [])
                elif event.get("type") == "done":
                    cited_sources = list(event.get("sources") or [])
                    duration_ms = int(event.get("duration_ms") or 0)
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except asyncio.CancelledError:
            final_status = "cancelled"
            raise
        except RagError as exc:
            final_status = exc.code
            logger.warning("chat_stream_error", extra={"code": exc.code})
            yield json.dumps(
                {
                    "type": "error",
                    "code": exc.code,
                    "detail": exc.user_message,
                    "request_id": request_id,
                },
                ensure_ascii=False,
            ) + "\n"
        except Exception:
            final_status = "internal_error"
            logger.exception("chat_stream_unhandled")
            yield json.dumps(
                {
                    "type": "error",
                    "code": "internal_error",
                    "detail": "Erro interno inesperado durante a geracao.",
                    "request_id": request_id,
                },
                ensure_ascii=False,
            ) + "\n"
        finally:
            gate.release()
            await audit.record(
                request_id=request_id,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="rag.query.stream",
                status=final_status,
                query_text=question,
                retrieved_sources=retrieved_sources,
                cited_sources=cited_sources,
                duration_ms=duration_ms,
                ip_address=_client_ip(request),
            )

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "X-Request-ID": request_id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
