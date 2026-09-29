"""Camada HTTP de defesa: cabeçalhos, limite de corpo e IP confiável.

Os middlewares aqui são ASGI puros, e não ``BaseHTTPMiddleware``, para não
bufferizar o corpo das respostas: ``/api/chat/stream`` precisa entregar cada
token assim que ele sai do modelo.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from functools import lru_cache

from fastapi import Request
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# A SPA é servida pelo próprio domínio e não embute recurso de terceiros, então
# tudo fica em 'self'. 'unsafe-inline' em style-src é a única concessão: o
# Angular injeta tags <style> em tempo de execução.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "base-uri 'self'",
        "script-src 'self'",
        # Angular injeta <style> em runtime; sem 'unsafe-inline' a app quebra.
        # Considerar migrar para nonces/hashes quando o build permitir.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        # Bloqueia service workers/manifest fora da própria origem — sem esses
        # 'self' explícitos, um manifest injetado poderia registrar o app
        # numa origem controlada por terceiro.
        "worker-src 'self'",
        "manifest-src 'self'",
        # Bloqueia qualquer child-frame (iframe) além da própria origem, e
        # ao mesmo tempo mantém 'frame-ancestors' para clickjacking.
        "frame-src 'none'",
        "child-src 'none'",
        # Impede navegação/prefetch fora da app.
        "navigate-to 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)

SECURITY_HEADERS: dict[str, str] = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "x-permitted-cross-domain-policies": "none",
}


@lru_cache(maxsize=1)
def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for entry in get_settings().trusted_proxy_cidrs:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("trusted_proxy_cidr_invalid", extra={"value": entry})
    return tuple(networks)


def _is_trusted_proxy(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _trusted_networks())


def client_ip(request: Request) -> str:
    """IP real do cliente, aceitando cabeçalhos apenas de proxies conhecidos.

    Se qualquer origem pudesse enviar ``X-Real-IP``, bastaria variar o cabeçalho
    a cada requisição para zerar o rate limit por IP.
    """
    peer = request.client.host if request.client else ""
    if peer and _is_trusted_proxy(peer):
        forwarded = (request.headers.get("x-real-ip") or "").strip()
        if not forwarded:
            chain = (request.headers.get("x-forwarded-for") or "").strip()
            forwarded = chain.split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    return peer or "unknown"


class SecurityHeadersMiddleware:
    """Carimba os cabeçalhos de defesa em toda resposta, inclusive nos erros."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.headers = dict(SECURITY_HEADERS)
        if settings.cookie_secure or settings.is_production:
            self.headers["strict-transport-security"] = (
                "max-age=31536000; includeSubDomains"
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_api = scope.get("path", "").startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    if name not in headers:
                        headers.append(name, value)
                # Resposta autenticada nunca pode ficar em cache compartilhado.
                if is_api and "cache-control" not in headers:
                    headers.append("cache-control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Corta corpos grandes antes de gastar memória lendo a requisição.

    O upload tem folga própria; as demais rotas só recebem JSON pequeno, então
    megabytes ali só podem ser tentativa de exaustão de recursos. O
    ``Content-Length`` é apenas o atalho: o total realmente recebido também é
    contado, senão bastaria usar ``Transfer-Encoding: chunked`` para escapar.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.json_limit = settings.max_json_body_bytes
        self.upload_limit = settings.max_upload_bytes + 1024 * 1024

    def _limit_for(self, path: str) -> int:
        return self.upload_limit if path.endswith("/documents/upload") else self.json_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope.get("path", ""))
        raw_length = Headers(scope=scope).get("content-length")
        if raw_length:
            try:
                declared = int(raw_length)
            except ValueError:
                await _send_error(send, 400, "invalid_request", "Content-Length inválido.")
                return
            if declared > limit:
                await _send_error(send, 413, "payload_too_large", _TOO_LARGE)
                return

        received = 0
        exceeded = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    # Encerra a leitura para o handler não seguir consumindo.
                    return {"type": "http.disconnect"}
            return message

        blocked = False

        async def guarded_send(message: Message) -> None:
            nonlocal blocked
            if blocked:
                return
            if exceeded and message["type"] == "http.response.start":
                blocked = True
                await _send_error(send, 413, "payload_too_large", _TOO_LARGE)
                return
            await send(message)

        await self.app(scope, counting_receive, guarded_send)


_TOO_LARGE = "O corpo da requisição excede o limite permitido."


async def _send_error(send: Send, status: int, code: str, detail: str) -> None:
    from app.logging_config import get_request_id

    payload = json.dumps(
        {"code": code, "detail": detail, "request_id": get_request_id()}
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
