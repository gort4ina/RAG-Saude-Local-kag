"""Controle de admissão para não saturar a GPU com gerações simultâneas."""

from __future__ import annotations

import asyncio
from functools import lru_cache

from fastapi import HTTPException, status

from app.config import get_settings
from app.services import metrics as prom_metrics


class InferenceGate:
    def __init__(self, concurrency: int, wait_seconds: float) -> None:
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._wait_seconds = max(0.01, wait_seconds)

    async def acquire(self) -> None:
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self._wait_seconds
            )
        except TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Capacidade de inferência ocupada. Tente novamente em instantes.",
                headers={"Retry-After": "5"},
            ) from exc
        prom_metrics.inference_gate_in_flight.inc()

    def release(self) -> None:
        self._semaphore.release()
        prom_metrics.inference_gate_in_flight.dec()


@lru_cache(maxsize=1)
def get_inference_gate() -> InferenceGate:
    settings = get_settings()
    return InferenceGate(
        settings.inference_max_concurrency,
        settings.inference_queue_wait_seconds,
    )

