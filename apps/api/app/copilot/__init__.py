"""Intelligence tool layer. Wraps existing services; does not replace them."""

from app.copilot.budget import BudgetTracker
from app.copilot.evidence import EvidenceClaim, EvidenceEnvelope
from app.copilot.registry import ToolCatalogEntry, ToolDefinition, ToolRegistry


def default_registry() -> ToolRegistry:
    from app.copilot.tools import register_all

    registry = ToolRegistry()
    register_all(registry)
    return registry


__all__ = [
    "BudgetTracker",
    "EvidenceClaim",
    "EvidenceEnvelope",
    "ToolCatalogEntry",
    "ToolDefinition",
    "ToolRegistry",
    "default_registry",
]
