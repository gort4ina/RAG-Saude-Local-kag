"""Active Harness — planner, evidence, repair (desligado por default).

Nao e importado pelo caminho de chat enquanto ``HARNESS_ENABLED`` for falso
ou ``HARNESS_MODE`` for ``evaluation``.
"""

from app.harness.active.evidence import EvidenceAssessment, assess_evidence
from app.harness.active.orchestrator import ActiveHarnessOrchestrator
from app.harness.active.planner import QueryCategory, plan_query
from app.harness.active.repair import RepairDecision, rewrite_query

__all__ = [
    "ActiveHarnessOrchestrator",
    "EvidenceAssessment",
    "QueryCategory",
    "RepairDecision",
    "assess_evidence",
    "plan_query",
    "rewrite_query",
]
