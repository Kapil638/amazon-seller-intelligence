"""Explicit registry of trusted intelligence tools. Unknown names cannot be executed."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.copilot.budget import COST_NONE, COST_OPENAI, COST_RAINFOREST_SEARCH, BudgetTracker
from app.copilot.evidence import EvidenceEnvelope
from app.copilot.exceptions import (
    BudgetRequiredError,
    ConfirmationRequiredError,
    ToolValidationError,
    UnknownToolError,
)

ToolHandler = Callable[[BaseModel], EvidenceEnvelope | Awaitable[EvidenceEnvelope]]
CostResolver = Callable[[BaseModel], str]

# Keys a model might put in tool JSON. They never grant permission.
_MODEL_PERMISSION_KEYS = frozenset({"confirmed", "budget", "handler"})


class ToolCatalogEntry(BaseModel):
    """Planner-visible contract. No handlers or other Python internals."""

    name: str
    description: str
    input_schema: dict[str, Any]
    cost: str
    confirmation_required: bool


@dataclass(frozen=True)
class ToolDefinition:
    """Internal registration record. Not returned to a planner."""

    name: str
    description: str
    input_schema: type[BaseModel]
    handler: ToolHandler
    requires_confirmation: bool = False
    estimated_provider_cost: str = COST_NONE
    cost_resolver: CostResolver | None = None

    def cost_kind(self, payload: BaseModel) -> str:
        if self.cost_resolver is not None:
            return self.cost_resolver(payload)
        return self.estimated_provider_cost


class ToolRegistry:
    """Name → tool. The only execution path a future Copilot planner may use."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if not tool.name.strip():
            raise ValueError("Tool name is required.")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> ToolCatalogEntry:
        """Public contract for a planner. Does not include the handler."""
        return _catalog_entry(self._definition(name))

    def list_tools(self) -> list[ToolCatalogEntry]:
        """Public catalog for a planner. Sorted by name. No handlers."""
        return [_catalog_entry(self._tools[name]) for name in sorted(self._tools)]

    def _definition(self, name: str) -> ToolDefinition:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(name)
        return tool

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any] | BaseModel | None = None,
        *,
        budget: BudgetTracker | None = None,
        confirmed: bool = False,
    ) -> EvidenceEnvelope:
        """Run a registered tool under a per-turn budget.

        `budget` is required. There is no unlimited execution path.

        `confirmed` is application permission, not a tool argument. Set it True
        only after a trusted seller confirmation step owned by server-side Copilot
        code (Milestone 11B). Never copy `confirmed` from model JSON into this
        parameter. A `confirmed` key inside `arguments` is ignored.
        """
        if budget is None:
            raise BudgetRequiredError()
        tool = self._definition(name)
        raw = _sanitize_arguments(arguments)
        try:
            payload = tool.input_schema.model_validate(raw)
        except ValidationError as exc:
            raise ToolValidationError(name, exc.errors()[0]["msg"]) from exc

        cost_kind = tool.cost_kind(payload)
        budget.assert_can_execute(COST_NONE, confirmed=True)
        needs_confirm = tool.requires_confirmation or budget.requires_confirmation(cost_kind)
        if needs_confirm and not confirmed:
            raise ConfirmationRequiredError(
                _confirm_text(cost_kind, tool.name),
                cost_kind=cost_kind,
            )
        result = tool.handler(payload)
        envelope = await result if isawaitable(result) else result
        budget.record_execution(cost_kind)
        return envelope


def _sanitize_arguments(arguments: dict[str, Any] | BaseModel | None) -> dict[str, Any]:
    if arguments is None:
        raw: dict[str, Any] = {}
    elif isinstance(arguments, BaseModel):
        raw = arguments.model_dump()
    else:
        raw = dict(arguments)
    for key in _MODEL_PERMISSION_KEYS:
        raw.pop(key, None)
    return raw


def _catalog_entry(tool: ToolDefinition) -> ToolCatalogEntry:
    confirmation_required = tool.requires_confirmation or tool.estimated_provider_cost in (
        COST_RAINFOREST_SEARCH,
        COST_OPENAI,
    )
    return ToolCatalogEntry(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema.model_json_schema(),
        cost=tool.estimated_provider_cost,
        confirmation_required=confirmation_required,
    )


def _confirm_text(cost_kind: str, tool_name: str) -> str:
    if cost_kind == "rainforest_product":
        return "This will look up another Amazon product (product credits). Continue?"
    if cost_kind == "rainforest_search":
        return "This will run an Amazon search (search credits). Continue?"
    if cost_kind == "openai":
        return "This will call OpenAI. Continue?"
    return f"Tool {tool_name} requires confirmation."
