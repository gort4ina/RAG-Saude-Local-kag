"""Flags do AI Harness carregadas do ambiente.

Defaults conservadores: Harness desligado e modo evaluation. O caminho de
chat da RAG/KAG nao muda enquanto ``enabled`` for falso.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool_env(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: str) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    """Configuracao do AI Harness."""

    enabled: bool = field(
        default_factory=lambda: _bool_env("HARNESS_ENABLED", "false")
    )
    # ``evaluation`` = so runners/metricas offline; ``active`` = Repair Loop.
    mode: str = field(
        default_factory=lambda: os.getenv("HARNESS_MODE", "evaluation").strip().lower()
    )
    max_retries: int = field(
        default_factory=lambda: _int_env("HARNESS_MAX_RETRIES", "2")
    )
    query_rewrite_enabled: bool = field(
        default_factory=lambda: _bool_env("HARNESS_QUERY_REWRITE_ENABLED", "true")
    )
    evidence_validation_enabled: bool = field(
        default_factory=lambda: _bool_env(
            "HARNESS_EVIDENCE_VALIDATION_ENABLED", "true"
        )
    )
    citation_validation_enabled: bool = field(
        default_factory=lambda: _bool_env(
            "HARNESS_CITATION_VALIDATION_ENABLED", "true"
        )
    )
    trace_enabled: bool = field(
        default_factory=lambda: _bool_env("HARNESS_TRACE_ENABLED", "true")
    )

    def __post_init__(self) -> None:
        if self.mode not in {"evaluation", "active"}:
            raise ValueError("HARNESS_MODE deve ser 'evaluation' ou 'active'.")
        if self.max_retries < 0:
            raise ValueError("HARNESS_MAX_RETRIES nao pode ser negativo.")

    @property
    def active_runtime_enabled(self) -> bool:
        """True somente quando o orquestrador pode interceptar o chat."""
        return self.enabled and self.mode == "active"


@lru_cache(maxsize=1)
def get_harness_settings() -> HarnessSettings:
    return HarnessSettings()
