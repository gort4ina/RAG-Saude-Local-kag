"""Cliente HTTP para a API local do Ollama.

Caracteristicas:

- uma unica instancia de ``httpx.AsyncClient`` reutilizada por todo o
  processo (aberta e fechada pelo lifespan do FastAPI), evitando um
  handshake TCP novo a cada pergunta;
- streaming real de ``POST /api/chat``;
- captura das metricas de latencia devolvidas pelo Ollama;
- erros diferenciados por causa (conexao, timeout, modelo ausente,
  resposta vazia, HTTP invalido, sobrecarga), sempre com a causa original
  preservada em ``__cause__``;
- verificacao de modelo por nome E tag: ``qwen2.5:7b`` NAO satisfaz
  ``qwen2.5:3b``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.errors import (
    ModelNotInstalledError,
    OllamaConnectionError,
    OllamaEmptyResponseError,
    OllamaHttpError,
    OllamaOverloadedError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    StreamInterruptedError,
)

logger = logging.getLogger(__name__)

_NS_PER_MS = 1_000_000

# Trechos que o Ollama devolve quando nao consegue alocar o modelo.
_OVERLOAD_MARKERS = (
    "out of memory",
    "requires more system memory",
    "insufficient memory",
    "no available",
    "server busy",
    "max queue",
)


def model_matches(installed: str, target: str) -> bool:
    """Verifica se ``installed`` satisfaz o modelo pedido em ``target``.

    Regras:

    - nome (parte antes do ``:``) precisa ser identico;
    - ``target`` sem tag aceita apenas ``latest``;
    - ``target`` com tag aceita a tag identica ou uma variante de
      quantizacao da MESMA tag (``qwen2.5:3b`` casa com
      ``qwen2.5:3b-instruct-q4_K_M``);
    - tags diferentes nunca casam: ``qwen2.5:7b`` nao satisfaz
      ``qwen2.5:3b``.
    """
    installed = installed.strip()
    target = target.strip()
    if not installed or not target:
        return False

    # /api/tags pode devolver "library/nome:tag" ou "registry/library/nome:tag".
    normalized = installed.rsplit("/", 1)[-1]

    if normalized == target:
        return True

    installed_name, _, installed_tag = normalized.partition(":")
    target_name, _, target_tag = target.partition(":")
    if installed_name != target_name:
        return False

    if not target_tag:
        return installed_tag in ("", "latest")
    if not installed_tag:
        return False
    return installed_tag == target_tag or installed_tag.startswith(f"{target_tag}-")


@dataclass(frozen=True, slots=True)
class OllamaMetrics:
    """Metricas de latencia devolvidas pelo Ollama (convertidas para ms)."""

    total_duration_ms: int = 0
    load_duration_ms: int = 0
    prompt_eval_count: int = 0
    prompt_eval_duration_ms: int = 0
    eval_count: int = 0
    eval_duration_ms: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OllamaMetrics:
        def ms(key: str) -> int:
            value = payload.get(key)
            return int(value // _NS_PER_MS) if isinstance(value, (int, float)) else 0

        def count(key: str) -> int:
            value = payload.get(key)
            return int(value) if isinstance(value, (int, float)) else 0

        return cls(
            total_duration_ms=ms("total_duration"),
            load_duration_ms=ms("load_duration"),
            prompt_eval_count=count("prompt_eval_count"),
            prompt_eval_duration_ms=ms("prompt_eval_duration"),
            eval_count=count("eval_count"),
            eval_duration_ms=ms("eval_duration"),
        )

    @property
    def tokens_per_second(self) -> float:
        if self.eval_duration_ms <= 0 or self.eval_count <= 0:
            return 0.0
        return round(self.eval_count / (self.eval_duration_ms / 1000), 2)

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "total_duration_ms": self.total_duration_ms,
            "load_duration_ms": self.load_duration_ms,
            "prompt_eval_count": self.prompt_eval_count,
            "prompt_eval_duration_ms": self.prompt_eval_duration_ms,
            "eval_count": self.eval_count,
            "eval_duration_ms": self.eval_duration_ms,
            "tokens_per_second": self.tokens_per_second,
        }


@dataclass(frozen=True, slots=True)
class ChatResult:
    """Resposta completa do modelo mais as metricas da geracao."""

    content: str
    metrics: OllamaMetrics = field(default_factory=OllamaMetrics)


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """Um passo do streaming: token parcial ou final com metricas."""

    content: str = ""
    done: bool = False
    metrics: OllamaMetrics | None = None


class OllamaClient:
    """Encapsula chamadas para o daemon do Ollama."""

    def __init__(
        self,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        *,
        health_timeout: float = 5.0,
        embed_timeout: float = 120.0,
        chat_timeout: float = 300.0,
        num_ctx: int = 4096,
        num_predict: int = 450,
        temperature: float = 0.1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.health_timeout = health_timeout
        self.embed_timeout = embed_timeout
        self.chat_timeout = chat_timeout
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.temperature = temperature
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Ciclo de vida (chamado pelo lifespan do FastAPI)
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Abre o pool de conexoes reutilizado por todo o processo."""
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            trust_env=False,
            timeout=httpx.Timeout(self.chat_timeout, connect=10.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
        logger.info("ollama_client_started", extra={"base_url": self.base_url})

    async def shutdown(self) -> None:
        """Fecha o pool de conexoes."""
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None
        logger.info("ollama_client_closed")

    @property
    def client(self) -> httpx.AsyncClient:
        """Cliente compartilhado; criado sob demanda fora do lifespan."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                trust_env=False,
                timeout=httpx.Timeout(self.chat_timeout, connect=10.0),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
        return self._client

    # ------------------------------------------------------------------
    # Traducao de excecoes httpx -> erros de dominio
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_overloaded(body: str) -> bool:
        lowered = body.lower()
        return any(marker in lowered for marker in _OVERLOAD_MARKERS)

    def _translate(
        self, exc: Exception, *, operation: str, model: str
    ) -> OllamaUnavailableError:
        """Converte uma falha de transporte no erro de dominio correspondente."""
        if isinstance(exc, httpx.TimeoutException):
            logger.error(
                f"ollama_{operation}_timeout",
                extra={"model": model, "timeout_s": self.chat_timeout},
            )
            return OllamaTimeoutError(
                f"Timeout ao executar '{operation}' com o modelo {model}."
            )

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            body = ""
            try:
                body = exc.response.text[:300]
            except Exception:  # pragma: no cover - resposta em streaming ja fechada
                body = ""
            if status == 404:
                logger.error(
                    f"ollama_{operation}_model_missing", extra={"model": model}
                )
                return ModelNotInstalledError(model)
            if status in (429, 503) or self._looks_overloaded(body):
                logger.error(
                    f"ollama_{operation}_overloaded",
                    extra={"model": model, "status": status},
                )
                return OllamaOverloadedError()
            logger.error(
                f"ollama_{operation}_http_error",
                extra={"model": model, "status": status, "body": body},
            )
            return OllamaHttpError(status)

        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            logger.error(
                f"ollama_{operation}_connection_error",
                extra={"model": model, "base_url": self.base_url},
            )
            return OllamaConnectionError()

        if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError)):
            logger.error(f"ollama_{operation}_stream_interrupted", extra={"model": model})
            return StreamInterruptedError()

        logger.exception(f"ollama_{operation}_transport_error", extra={"model": model})
        return OllamaConnectionError()

    @staticmethod
    def _raise_if_payload_error(payload: dict[str, Any], model: str) -> None:
        """O Ollama pode devolver HTTP 200 com ``{"error": "..."}``."""
        error = payload.get("error")
        if not error:
            return
        text = str(error)
        lowered = text.lower()
        if "not found" in lowered or "try pulling" in lowered:
            raise ModelNotInstalledError(model)
        if OllamaClient._looks_overloaded(text):
            raise OllamaOverloadedError()
        raise OllamaUnavailableError(text)

    # ------------------------------------------------------------------
    # Operacoes
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Verifica se o processo Ollama esta acessivel. Nao levanta excecoes."""
        try:
            response = await self.client.get("/api/tags", timeout=self.health_timeout)
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("ollama_health_failed", extra={"error": type(exc).__name__})
            return False

    async def list_installed_models(self) -> list[str]:
        """Retorna a lista de modelos ja baixados. Nao levanta excecoes."""
        try:
            response = await self.client.get("/api/tags", timeout=self.health_timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning(
                "ollama_list_models_failed", extra={"error": type(exc).__name__}
            )
            return []
        if not isinstance(data, dict):
            return []
        models = data.get("models") or []
        return [
            str(item.get("model") or item.get("name") or "")
            for item in models
            if isinstance(item, dict) and (item.get("model") or item.get("name"))
        ]

    async def has_model(self, model: str) -> bool:
        """True apenas se nome E tag do modelo estiverem instalados."""
        installed = await self.list_installed_models()
        return any(model_matches(name, model) for name in installed)

    async def count_tokens(self, text: str) -> int:
        """Estimativa real de tokens usando ``/api/tokenize``.

        A rota chegou em versões recentes do Ollama; se estiver indisponível
        (404) ou falhar, caímos numa heurística conservadora baseada em
        caracteres (``len // 4``) para nunca travar o pipeline por conta do
        contador.
        """
        if not text:
            return 0
        try:
            response = await self.client.post(
                "/api/tokenize",
                json={"model": self.chat_model, "content": text},
                timeout=self.health_timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.debug(
                "ollama_tokenize_unavailable",
                extra={"error": type(exc).__name__},
            )
            return max(1, len(text) // 4)
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if isinstance(tokens, list):
            return len(tokens)
        # Servidor pode devolver apenas contagem
        count = payload.get("count") if isinstance(payload, dict) else None
        if isinstance(count, int):
            return count
        return max(1, len(text) // 4)

    async def loaded_models(self) -> list[dict[str, Any]]:
        """Modelos residentes em memoria (equivalente a ``ollama ps``)."""
        try:
            response = await self.client.get("/api/ps", timeout=self.health_timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("ollama_ps_failed", extra={"error": type(exc).__name__})
            return []
        if not isinstance(data, dict):
            return []
        return [item for item in (data.get("models") or []) if isinstance(item, dict)]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Converte textos em vetores usando o modelo de embeddings."""
        if not texts:
            return []
        payload = {"model": self.embedding_model, "input": texts}
        try:
            response = await self.client.post(
                "/api/embed", json=payload, timeout=self.embed_timeout
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise self._translate(
                exc, operation="embed", model=self.embedding_model
            ) from exc

        if not isinstance(data, dict):
            raise OllamaUnavailableError("Ollama retornou um payload inesperado.")
        self._raise_if_payload_error(data, self.embedding_model)

        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            logger.error(
                "ollama_embed_bad_shape",
                extra={"model": self.embedding_model, "count": len(texts)},
            )
            raise OllamaUnavailableError(
                "Ollama retornou embeddings em formato inesperado."
            )
        return vectors

    def _chat_payload(
        self, system_prompt: str, user_prompt: str, *, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": self.chat_model,
            "stream": stream,
            "keep_alive": "10m",
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

    async def chat(self, system_prompt: str, user_prompt: str) -> ChatResult:
        """Resposta completa (nao streaming) com as metricas da geracao."""
        payload = self._chat_payload(system_prompt, user_prompt, stream=False)
        try:
            response = await self.client.post(
                "/api/chat", json=payload, timeout=self.chat_timeout
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise self._translate(
                exc, operation="chat", model=self.chat_model
            ) from exc

        if not isinstance(data, dict):
            raise OllamaUnavailableError("Ollama retornou um payload inesperado.")
        self._raise_if_payload_error(data, self.chat_model)

        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            logger.error("ollama_chat_empty", extra={"model": self.chat_model})
            raise OllamaEmptyResponseError()

        metrics = OllamaMetrics.from_payload(data)
        logger.info(
            "ollama_chat_metrics",
            extra={"model": self.chat_model, **metrics.as_log_fields()},
        )
        return ChatResult(content=content.strip(), metrics=metrics)

    async def chat_stream(
        self, system_prompt: str, user_prompt: str
    ) -> AsyncIterator[StreamEvent]:
        """Streaming real: emite cada delta assim que o Ollama o produz."""
        payload = self._chat_payload(system_prompt, user_prompt, stream=True)
        produced = False
        try:
            async with self.client.stream(
                "POST", "/api/chat", json=payload, timeout=self.chat_timeout
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    response.raise_for_status()

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("ollama_stream_bad_line")
                        continue
                    if not isinstance(chunk, dict):
                        continue

                    self._raise_if_payload_error(chunk, self.chat_model)

                    delta = (chunk.get("message") or {}).get("content") or ""
                    if delta:
                        produced = True
                        yield StreamEvent(content=delta)

                    if chunk.get("done"):
                        metrics = OllamaMetrics.from_payload(chunk)
                        logger.info(
                            "ollama_chat_stream_metrics",
                            extra={
                                "model": self.chat_model,
                                **metrics.as_log_fields(),
                            },
                        )
                        yield StreamEvent(done=True, metrics=metrics)
                        return
        except OllamaUnavailableError:
            raise
        except Exception as exc:
            raise self._translate(
                exc, operation="chat_stream", model=self.chat_model
            ) from exc

        if not produced:
            logger.error("ollama_chat_stream_empty", extra={"model": self.chat_model})
            raise OllamaEmptyResponseError()
