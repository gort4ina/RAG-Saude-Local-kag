"""InferenceGate: a vaga so pode ser devolvida uma vez."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.inference_gate import InferenceGate


@pytest.mark.asyncio
async def test_lease_release_is_idempotent() -> None:
    """Gerador e tarefa de segundo plano devolvem a mesma vaga sem duplicar."""
    gate = InferenceGate(concurrency=1, wait_seconds=0.01)

    lease = await gate.acquire()
    lease.release()
    lease.release()  # segunda devolucao nao pode abrir uma vaga extra

    first = await gate.acquire()
    with pytest.raises(HTTPException) as busy:
        await gate.acquire()  # com concorrencia 1 so existe uma vaga
    assert busy.value.status_code == 429
    first.release()


@pytest.mark.asyncio
async def test_released_lease_frees_the_gate_for_the_next_request() -> None:
    """Cenario do streaming cancelado antes do gerador rodar: background libera."""
    gate = InferenceGate(concurrency=1, wait_seconds=0.01)

    lease = await gate.acquire()
    with pytest.raises(HTTPException):
        await gate.acquire()

    lease.release()  # o que o BackgroundTask da resposta faz

    again = await gate.acquire()
    again.release()
