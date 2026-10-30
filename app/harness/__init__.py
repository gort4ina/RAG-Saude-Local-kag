"""AI Harness — avaliacao offline e orquestracao ativa (feature-flagged).

O Evaluation Harness vive em ``app.harness.evaluation`` e ``app.harness.metrics``.
O Active Harness (``app.harness.active``) so entra no caminho de chat quando
``HARNESS_ENABLED=true`` e ``HARNESS_MODE=active``.
"""

from __future__ import annotations

from app.harness.settings import HarnessSettings, get_harness_settings

__all__ = ["HarnessSettings", "get_harness_settings"]
