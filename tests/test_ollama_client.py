"""Testes do OllamaClient contra um transporte httpx falso."""

from __future__ import annotations

import httpx
import pytest

from app.errors import (
    ModelNotInstalledError,
    OllamaConnectionError,
    OllamaEmptyResponseError,
    OllamaHttpError,
    OllamaOverloadedError,
    OllamaTimeoutError,
)
from app.services.ollama_client import OllamaClient, OllamaMetrics, model_matches


def _make_client(handler, **kwargs) -> OllamaClient:
    """Cliente com o pool ja aberto sobre um MockTransport."""
    client = OllamaClient(
        "http://ollama:11434",
        chat_model=kwargs.pop("chat_model", "qwen2.5:3b"),
        embedding_model=kwargs.pop("embedding_model", "embeddinggemma"),
        health_timeout=1.0,
        embed_timeout=1.0,
        chat_timeout=1.0,
        **kwargs,
    )
    client._client = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(handler)
    )
    return client


# ---------------------------------------------------------------------------
# Verificacao de nome e tag do modelo
# ---------------------------------------------------------------------------


def test_model_matches_requires_same_tag() -> None:
    assert model_matches("qwen2.5:3b", "qwen2.5:3b") is True
    # A regra central: 7b nunca satisfaz 3b.
    assert model_matches("qwen2.5:7b", "qwen2.5:3b") is False
    assert model_matches("qwen2.5:3b", "qwen2.5:7b") is False


def test_model_matches_accepts_quantization_variant_of_same_tag() -> None:
    assert model_matches("qwen2.5:3b-instruct-q4_K_M", "qwen2.5:3b") is True
    assert model_matches("qwen2.5:7b-instruct-q4_K_M", "qwen2.5:3b") is False


def test_model_matches_untagged_target_means_latest() -> None:
    assert model_matches("embeddinggemma:latest", "embeddinggemma") is True
    assert model_matches("embeddinggemma:300m", "embeddinggemma") is False
    assert model_matches("library/embeddinggemma:latest", "embeddinggemma") is True


def test_model_matches_rejects_different_names() -> None:
    assert model_matches("bge-m3:latest", "embeddinggemma") is False


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_returns_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[1.0, 2.0]]})

    client = _make_client(handler)
    try:
        assert await client.embed(["oi"]) == [[1.0, 2.0]]
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_embed_404_becomes_model_not_installed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    client = _make_client(handler)
    try:
        with pytest.raises(ModelNotInstalledError):
            await client.embed(["oi"])
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_embed_timeout_becomes_ollama_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = _make_client(handler)
    try:
        with pytest.raises(OllamaTimeoutError) as info:
            await client.embed(["oi"])
        assert isinstance(info.value.__cause__, httpx.ReadTimeout)
    finally:
        await client.shutdown()


# ---------------------------------------------------------------------------
# Chat nao streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_timeout_becomes_ollama_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = _make_client(handler)
    try:
        with pytest.raises(OllamaTimeoutError):
            await client.chat("system", "user")
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_chat_connection_error_is_specific() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado", request=request)

    client = _make_client(handler)
    try:
        with pytest.raises(OllamaConnectionError):
            await client.chat("system", "user")
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_chat_503_becomes_overloaded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="server busy")

    client = _make_client(handler)
    try:
        with pytest.raises(OllamaOverloadedError):
            await client.chat("system", "user")
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_chat_500_becomes_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _make_client(handler)
    try:
        with pytest.raises(OllamaHttpError):
            await client.chat("system", "user")
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_chat_empty_content_raises_empty_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "   "}, "done": True})

    client = _make_client(handler)
    try:
        with pytest.raises(OllamaEmptyResponseError):
            await client.chat("system", "user")
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_chat_captures_metrics_and_options() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured.update(_json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {"content": "resposta [Fonte 1]"},
                "done": True,
                "total_duration": 5_000_000_000,
                "load_duration": 1_200_000_000,
                "prompt_eval_count": 640,
                "prompt_eval_duration": 800_000_000,
                "eval_count": 120,
                "eval_duration": 3_000_000_000,
            },
        )

    client = _make_client(handler)
    try:
        result = await client.chat("system", "user")
    finally:
        await client.shutdown()

    assert result.content == "resposta [Fonte 1]"
    assert captured["options"]["num_ctx"] == 4096
    assert captured["options"]["num_predict"] == 450
    assert captured["options"]["temperature"] == 0.1
    assert captured["stream"] is False

    assert result.metrics.total_duration_ms == 5_000
    assert result.metrics.load_duration_ms == 1_200
    assert result.metrics.prompt_eval_count == 640
    assert result.metrics.prompt_eval_duration_ms == 800
    assert result.metrics.eval_count == 120
    assert result.metrics.eval_duration_ms == 3_000
    assert result.metrics.tokens_per_second == 40.0


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def _ndjson_handler(lines: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="".join(lines).encode("utf-8"))

    return handler


@pytest.mark.asyncio
async def test_chat_stream_yields_tokens_then_metrics() -> None:
    handler = _ndjson_handler(
        [
            '{"message":{"content":"Con"},"done":false}\n',
            '{"message":{"content":"forme"},"done":false}\n',
            '{"message":{"content":" [Fonte 1]"},"done":false}\n',
            '{"done":true,"eval_count":10,"eval_duration":1000000000,'
            '"load_duration":500000000}\n',
        ]
    )
    client = _make_client(handler)
    tokens: list[str] = []
    metrics = None
    try:
        async for event in client.chat_stream("system", "user"):
            if event.content:
                tokens.append(event.content)
            if event.done:
                metrics = event.metrics
    finally:
        await client.shutdown()

    assert "".join(tokens) == "Conforme [Fonte 1]"
    assert metrics is not None
    assert metrics.eval_count == 10
    assert metrics.load_duration_ms == 500
    assert metrics.tokens_per_second == 10.0


@pytest.mark.asyncio
async def test_chat_stream_without_tokens_raises_empty_response() -> None:
    handler = _ndjson_handler(['{"message":{"content":""},"done":false}\n'])
    client = _make_client(handler)
    try:
        with pytest.raises(OllamaEmptyResponseError):
            async for _ in client.chat_stream("system", "user"):
                pass
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_chat_stream_model_missing_in_payload() -> None:
    handler = _ndjson_handler(['{"error":"model \'x\' not found, try pulling it"}\n'])
    client = _make_client(handler)
    try:
        with pytest.raises(ModelNotInstalledError):
            async for _ in client.chat_stream("system", "user"):
                pass
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_chat_stream_cancellation_closes_cleanly() -> None:
    handler = _ndjson_handler(
        [
            '{"message":{"content":"a"},"done":false}\n',
            '{"message":{"content":"b"},"done":false}\n',
            '{"done":true}\n',
        ]
    )
    client = _make_client(handler)
    received: list[str] = []
    try:
        stream = client.chat_stream("system", "user")
        async for event in stream:
            received.append(event.content)
            break  # consumidor desiste no primeiro token
        await stream.aclose()
    finally:
        await client.shutdown()

    assert received == ["a"]


# ---------------------------------------------------------------------------
# Inventario de modelos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_installed_models_and_has_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {"model": "qwen2.5:3b", "name": "qwen2.5:3b"},
                    {"model": "embeddinggemma:latest", "name": "embeddinggemma:latest"},
                ]
            },
        )

    client = _make_client(handler)
    try:
        models = await client.list_installed_models()
        assert "qwen2.5:3b" in models
        assert await client.has_model("embeddinggemma") is True
        assert await client.has_model("qwen2.5:7b") is False
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_loaded_models_reports_vram() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ps"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"model": "qwen2.5:3b", "size": 2_400_000_000, "size_vram": 2_400_000_000}
                ]
            },
        )

    client = _make_client(handler)
    try:
        loaded = await client.loaded_models()
    finally:
        await client.shutdown()

    assert loaded[0]["size_vram"] == 2_400_000_000


def test_metrics_from_empty_payload_is_zeroed() -> None:
    metrics = OllamaMetrics.from_payload({})
    assert metrics.total_duration_ms == 0
    assert metrics.tokens_per_second == 0.0
