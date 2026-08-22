"""Execute a validated Plan through ToolRegistry only. Does not synthesize answers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from app.copilot import default_registry
from app.copilot.budget import COST_NONE, BudgetTracker
from app.copilot.conversation.service import ConversationService, PendingConfirmationRecord
from app.copilot.exceptions import (
    BudgetExceededError,
    ConfirmationRequiredError,
    CopilotToolError,
    ToolValidationError,
    UnknownToolError,
)
from app.copilot.orchestrator.schemas import (
    PAID_TOOLS,
    ConfirmRequest,
    ConfirmationGrant,
    ExecutionRequest,
    ExecutionResult,
    ToolCallResult,
)
from app.copilot.planner.schemas import Plan
from app.copilot.registry import ToolRegistry
from app.core.exceptions import (
    ConfirmationNonceConsumedError,
    ConfirmationNonceExpiredError,
    ConfirmationNonceInvalidError,
    PlanHashMismatchError,
    PlanInvalidError,
    PlanNotFoundError,
)
from app.persistence.database import current_organization_id


class ConfirmationGate:
    """Seller nonce + plan hash. The only path to confirmed=True."""

    def requires_confirmation(self, plan: Plan, registry: ToolRegistry) -> bool:
        if not plan.tool_calls:
            return False
        if plan.needs_confirmation:
            return True
        for call in plan.tool_calls:
            if call.name in PAID_TOOLS:
                return True
            try:
                cost = registry.get_tool(call.name).cost
            except UnknownToolError:
                continue
            if cost and cost != COST_NONE:
                return True
        return False

    def validate_nonce(
        self,
        *,
        pending: PendingConfirmationRecord | None,
        conversation_id: UUID,
        plan: Plan,
        nonce: str,
    ) -> ConfirmationGrant:
        if pending is None or pending.nonce != nonce:
            raise ConfirmationNonceInvalidError()
        if pending.conversation_id != conversation_id:
            raise ConfirmationNonceInvalidError()
        if pending.consumed_at is not None:
            raise ConfirmationNonceConsumedError()
        if _expired(pending.expires_at):
            raise ConfirmationNonceExpiredError()
        if pending.plan_id != plan.plan_id:
            raise PlanHashMismatchError()
        if pending.plan_hash != plan.plan_hash:
            raise PlanHashMismatchError()
        if pending.plan_schema_version and pending.plan_schema_version != plan.plan_schema_version:
            raise PlanHashMismatchError()
        return ConfirmationGrant(
            nonce=pending.nonce,
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            conversation_id=conversation_id,
        )


class OrchestratorService:
    """Confirmation gate → BudgetTracker → ToolRegistry.execute. Stops at envelopes."""

    def __init__(
        self,
        *,
        conversations: ConversationService | None = None,
        registry: ToolRegistry | None = None,
        gate: ConfirmationGate | None = None,
    ) -> None:
        self._conversations = conversations or ConversationService()
        self._registry = registry or default_registry()
        self._gate = gate or ConfirmationGate()

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        plan = self._load_plan(request.conversation_id, request.plan_id)
        self._assert_plan_matches(plan, request)
        needs_confirm = self._gate.requires_confirmation(plan, self._registry)
        grant: ConfirmationGrant | None = None
        # `request.confirmed` is untrusted client JSON and is never passed to execute().
        if needs_confirm:
            if not request.confirmation_nonce:
                return self._block_for_confirmation(plan)
            pending = self._conversations.get_pending_by_nonce(request.confirmation_nonce)
            grant = self._gate.validate_nonce(
                pending=pending,
                conversation_id=request.conversation_id,
                plan=plan,
                nonce=request.confirmation_nonce,
            )
            if grant.plan_hash != request.plan_hash:
                raise PlanHashMismatchError()
            self._conversations.consume_pending(grant.nonce)
            confirmed = True
        else:
            confirmed = False
            if request.confirmation_nonce:
                # Free plans do not consume or honor a nonce.
                pass

        return await self._run_plan(plan, confirmed=confirmed)

    async def confirm(self, conversation_id: UUID, payload: ConfirmRequest) -> ExecutionResult:
        pending = self._conversations.get_pending_by_nonce(payload.nonce)
        if pending is None:
            raise ConfirmationNonceInvalidError()
        if pending.conversation_id != conversation_id:
            raise ConfirmationNonceInvalidError()
        if pending.plan_id is None or not pending.plan_hash:
            raise ConfirmationNonceInvalidError()
        request = ExecutionRequest(
            plan_id=pending.plan_id,
            conversation_id=conversation_id,
            plan_hash=pending.plan_hash,
            confirmation_nonce=payload.nonce,
            confirmed=False,
        )
        return await self.execute(request)

    def _load_plan(self, conversation_id: UUID, plan_id: UUID) -> Plan:
        payload = self._conversations.get_plan_payload(conversation_id, plan_id)
        if payload is None:
            raise PlanNotFoundError(str(plan_id))
        try:
            return Plan.model_validate(payload)
        except ValidationError as exc:
            raise PlanInvalidError("The stored plan is not executable.") from exc

    def _assert_plan_matches(self, plan: Plan, request: ExecutionRequest) -> None:
        if plan.conversation_id != request.conversation_id:
            raise PlanInvalidError("The plan does not belong to this conversation.")
        if plan.organization_id != current_organization_id():
            raise PlanInvalidError("The plan does not belong to this organization.")
        if plan.plan_hash != request.plan_hash:
            raise PlanHashMismatchError()

    def _block_for_confirmation(self, plan: Plan) -> ExecutionResult:
        pending = self._conversations.issue_pending_confirmation(
            plan.conversation_id,
            plan_id=plan.plan_id,
            plan_schema_version=plan.plan_schema_version,
            plan_hash=plan.plan_hash,
            summary=plan.confirm_summary,
        )
        tool_results = [
            ToolCallResult(
                name=call.name,
                status="blocked_confirmation",
                error_code="confirmation_required",
                error_message=plan.confirm_summary,
            )
            for call in plan.tool_calls
        ]
        return ExecutionResult(
            plan_id=plan.plan_id,
            conversation_id=plan.conversation_id,
            organization_id=current_organization_id(),
            plan_hash=plan.plan_hash,
            status="blocked_confirmation",
            confirmation_required=True,
            confirmation_nonce=pending.nonce,
            confirm_summary=plan.confirm_summary,
            evidence=[],
            tool_results=tool_results,
        )

    async def _run_plan(self, plan: Plan, *, confirmed: bool) -> ExecutionResult:
        budget = BudgetTracker(
            max_tool_rounds=plan.budget_snapshot.max_tool_rounds,
            max_tools_per_turn=plan.budget_snapshot.max_tools_per_turn,
        )
        budget.begin_round()
        tool_results: list[ToolCallResult] = []
        evidence = []
        failed = False
        for call in plan.tool_calls:
            if failed:
                tool_results.append(
                    ToolCallResult(name=call.name, status="skipped", error_code="previous_tool_failed")
                )
                continue
            name, arguments = call.name, dict(call.arguments or {})
            arguments.pop("confirmed", None)
            arguments.pop("budget", None)
            arguments.pop("handler", None)
            try:
                envelope = await self._registry.execute(
                    name,
                    arguments,
                    budget=budget,
                    confirmed=confirmed,
                )
            except ConfirmationRequiredError as exc:
                failed = True
                tool_results.append(
                    ToolCallResult(
                        name=name,
                        status="blocked_confirmation",
                        error_code="confirmation_required",
                        error_message=str(exc),
                    )
                )
                continue
            except UnknownToolError as exc:
                failed = True
                tool_results.append(
                    ToolCallResult(name=name, status="failed", error_code="unknown_tool", error_message=str(exc))
                )
                continue
            except ToolValidationError as exc:
                failed = True
                tool_results.append(
                    ToolCallResult(
                        name=name, status="failed", error_code="invalid_arguments", error_message=str(exc)
                    )
                )
                continue
            except BudgetExceededError as exc:
                failed = True
                tool_results.append(
                    ToolCallResult(
                        name=name, status="failed", error_code="budget_exceeded", error_message=str(exc)
                    )
                )
                continue
            except CopilotToolError as exc:
                failed = True
                tool_results.append(
                    ToolCallResult(
                        name=name, status="failed", error_code="tool_error", error_message=str(exc)
                    )
                )
                continue
            except Exception as exc:
                failed = True
                tool_results.append(
                    ToolCallResult(
                        name=name, status="failed", error_code="tool_execution_failed", error_message=str(exc)
                    )
                )
                continue
            evidence.append(envelope)
            tool_results.append(ToolCallResult(name=name, status="succeeded", evidence=envelope))

        status: str = "failed" if failed else "succeeded"
        result = ExecutionResult(
            plan_id=plan.plan_id,
            conversation_id=plan.conversation_id,
            organization_id=current_organization_id(),
            plan_hash=plan.plan_hash,
            status=status,  # type: ignore[arg-type]
            confirmation_required=False,
            evidence=evidence,
            tool_results=tool_results,
        )
        self._persist_execution(plan, result)
        return result

    def _persist_execution(self, plan: Plan, result: ExecutionResult) -> None:
        refs = [
            {
                "evidence_id": str(item.evidence_id),
                "claim_keys": [claim.key for claim in item.claims],
            }
            for item in result.evidence
        ]
        self._conversations.add_message(
            plan.conversation_id,
            role="system",
            content="Copilot tool execution (evidence only; not a seller answer).",
            structured_payload={
                "type": "copilot_execution",
                "plan_id": str(plan.plan_id),
                "plan_hash": plan.plan_hash,
                "status": result.status,
                "evidence_refs": refs,
                "tool_results": [
                    {
                        "name": item.name,
                        "status": item.status,
                        "error_code": item.error_code,
                        "evidence_id": str(item.evidence.evidence_id) if item.evidence else None,
                    }
                    for item in result.tool_results
                ],
            },
        )


def get_orchestrator_service() -> OrchestratorService:
    return OrchestratorService()


def _expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    now = datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now
