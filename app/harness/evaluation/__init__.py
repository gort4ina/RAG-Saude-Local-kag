"""Evaluation Harness — dataset, runner offline e reports."""

from app.harness.evaluation.dataset import load_evaluation_cases
from app.harness.evaluation.reporter import write_report
from app.harness.evaluation.runner import (
    VARIANT_LABELS,
    build_service_for_variant,
    compare_variant_reports,
    evaluate_variant,
    options_snapshot,
)

__all__ = [
    "VARIANT_LABELS",
    "build_service_for_variant",
    "compare_variant_reports",
    "evaluate_variant",
    "load_evaluation_cases",
    "options_snapshot",
    "write_report",
]
