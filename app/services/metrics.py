"""Métricas Prometheus expostas em ``/api/metrics``.

Escolhas conscientes:

- **Histogramas com buckets do domínio.** As faixas do ``ml_stage_duration``
  cobrem o real: um embed local roda em 50–200 ms, um vetor query fica
  abaixo de 100 ms, o KAG em torno de 20 ms, e o chat local pode ir de 2 a
  30 segundos dependendo do tamanho do prompt e da memória disponível.
  Buckets fora dessa realidade agregariam medições demais no mesmo balde
  e mascariam a cauda longa.

- **Cardinalidade sob controle.** ``tenant`` **não** é label: em ambientes
  multi-tenant grandes ele explode a série temporal do Prometheus. Se um
  operador quiser recortar por tenant, o audit event guarda o dado. As
  labels aqui são apenas ``stage`` e ``model``.

- **Contadores separados por resultado.** ``rag_answers_total`` distingue
  ``grounded``, ``needs_review`` e ``no_context``. Alertar por
  ``needs_review > k%`` é indicativo de degradação do índice ou do LLM.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Registry único para o processo (multi-process não é usado hoje; para
# uvicorn --workers > 1, plugar ``multiprocess.MultiProcessCollector``).
REGISTRY = CollectorRegistry(auto_describe=True)


ml_stage_duration = Histogram(
    "rag_stage_duration_seconds",
    "Duração das etapas do pipeline RAG (embed, vector, bm25, graph, chat).",
    labelnames=("stage",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=REGISTRY,
)

chat_tokens_per_second = Histogram(
    "rag_chat_tokens_per_second",
    "Taxa de geração do modelo de chat (tokens/s) por resposta concluída.",
    buckets=(5, 10, 20, 40, 60, 100, 150, 250, 500),
    registry=REGISTRY,
)

chat_eval_count = Histogram(
    "rag_chat_eval_count",
    "Tokens gerados por resposta.",
    buckets=(50, 100, 200, 400, 800, 1600, 3200),
    registry=REGISTRY,
)

answers_total = Counter(
    "rag_answers_total",
    "Total de respostas por resultado da auditoria de citação.",
    labelnames=("outcome",),
    registry=REGISTRY,
)

# Uso de VRAM/RAM do Ollama, atualizado por polling em ``/api/status``.
ollama_model_size_bytes = Gauge(
    "rag_ollama_model_size_bytes",
    "Tamanho total do modelo residente no Ollama.",
    labelnames=("model",),
    registry=REGISTRY,
)
ollama_model_vram_bytes = Gauge(
    "rag_ollama_model_vram_bytes",
    "Bytes do modelo em VRAM (0 significa CPU-only).",
    labelnames=("model",),
    registry=REGISTRY,
)

# BM25/Chunks por tenant não são labels: exponho um agregado global.
knowledge_chunks_total = Gauge(
    "rag_knowledge_chunks_total",
    "Total de chunks indexados no store vetorial (todos os tenants).",
    registry=REGISTRY,
)

# Concorrência do gate de inferência: útil para dimensionar
# INFERENCE_MAX_CONCURRENCY.
inference_gate_in_flight = Gauge(
    "rag_inference_gate_in_flight",
    "Chamadas em andamento sob o InferenceGate.",
    registry=REGISTRY,
)


def observe_stage(stage: str, seconds: float) -> None:
    """Registra a duração de uma etapa. Aceita floats <= 0 sem estourar."""
    if seconds < 0:
        seconds = 0.0
    ml_stage_duration.labels(stage=stage).observe(seconds)


def observe_answer(outcome: str) -> None:
    """``outcome`` esperado: ``grounded``, ``needs_review``, ``no_context``."""
    answers_total.labels(outcome=outcome).inc()


def observe_chat_metrics(*, tokens_per_second: float, eval_count: int) -> None:
    if tokens_per_second > 0:
        chat_tokens_per_second.observe(tokens_per_second)
    if eval_count > 0:
        chat_eval_count.observe(eval_count)


def render() -> tuple[bytes, str]:
    """Retorna o corpo e o content-type para ``/api/metrics``."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


__all__ = [
    "REGISTRY",
    "answers_total",
    "chat_eval_count",
    "chat_tokens_per_second",
    "inference_gate_in_flight",
    "knowledge_chunks_total",
    "ml_stage_duration",
    "observe_answer",
    "observe_chat_metrics",
    "observe_stage",
    "ollama_model_size_bytes",
    "ollama_model_vram_bytes",
    "render",
]
