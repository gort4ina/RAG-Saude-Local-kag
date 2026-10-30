"""Gravacao padronizada de relatorios do Evaluation Harness."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_REPORTS_DIR = (
    Path(__file__).resolve().parents[3] / "eval" / "reports"
)


def write_report(
    payload: dict[str, Any],
    output: Path,
    *,
    measured: bool = True,
    harness_mode: str = "evaluation",
) -> Path:
    """Escreve JSON de report com metadados obrigatorios.

    ``measured=True`` marca resultados de execucao real. Nunca chame com
    ``measured=True`` para dados simulados.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "harness_mode": harness_mode,
        "measured": measured,
        **payload,
    }
    output.write_text(
        json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return output
