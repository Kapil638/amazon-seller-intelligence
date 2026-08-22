from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.copilot.conversation.service import ConversationService, get_conversation_service
from app.copilot.orchestrator.schemas import ConfirmRequest, ExecuteTurnRequest, ExecutionRequest, ExecutionResult
from app.copilot.orchestrator.service import OrchestratorService, get_orchestrator_service
from app.copilot.planner.schemas import Plan, PlanTurnRequest
from app.copilot.planner.service import PlannerService, get_planner_service
from app.copilot.synthesis.schemas import SynthesisRequest, SynthesizedResponse
from app.copilot.synthesis.service import SynthesisService, get_synthesis_service
from app.core.exceptions import (
    ConfirmationNonceConsumedError,
    ConfirmationNonceExpiredError,
    ConfirmationNonceInvalidError,
    ConversationNotFoundError,
    PersistenceNotConfiguredError,
    PlanHashMismatchError,
    PlanInvalidError,
    PlanNotFoundError,
)
from app.models.copilot_conversation import (
    ConversationDetail,
    ConversationListResponse,
    CopilotConversationCreate,
)

router = APIRouter(prefix="/api/v1/copilot", tags=["copilot"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ConversationNotFoundError | PlanNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, PlanHashMismatchError | ConfirmationNonceConsumedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(
        exc,
        ConfirmationNonceInvalidError
        | ConfirmationNonceExpiredError
        | PlanInvalidError,
    ):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.post("/conversations", response_model=ConversationDetail, status_code=201)
@router.post("/conversations/", response_model=ConversationDetail, status_code=201, include_in_schema=False)
def create_conversation(
    payload: CopilotConversationCreate | None = None,
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationDetail:
    try:
        return service.create_conversation(payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/conversations", response_model=ConversationListResponse)
@router.get("/conversations/", response_model=ConversationListResponse, include_in_schema=False)
def list_conversations(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationListResponse:
    try:
        return service.list_conversations(offset=offset, limit=limit)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: UUID,
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationDetail:
    try:
        return service.get_conversation(conversation_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/conversations/{conversation_id}/plan", response_model=Plan)
async def create_conversation_plan(
    conversation_id: UUID,
    payload: PlanTurnRequest,
    service: PlannerService = Depends(get_planner_service),
) -> Plan:
    """Propose and validate a Plan. Does not execute tools or synthesize a seller answer."""
    try:
        return await service.plan_turn(conversation_id, payload.user_message)
    except Exception as copilot_exc:
        raise _http_error(copilot_exc) from copilot_exc


@router.post("/conversations/{conversation_id}/execute", response_model=ExecutionResult)
async def execute_conversation_plan(
    conversation_id: UUID,
    payload: ExecuteTurnRequest,
    service: OrchestratorService = Depends(get_orchestrator_service),
) -> ExecutionResult:
    """Execute a hashed Plan through ToolRegistry. Does not synthesize a seller answer."""
    request = ExecutionRequest(
        plan_id=payload.plan_id,
        conversation_id=conversation_id,
        plan_hash=payload.plan_hash,
        confirmation_nonce=payload.confirmation_nonce,
        confirmed=payload.confirmed,
    )
    try:
        return await service.execute(request)
    except Exception as copilot_exc:
        raise _http_error(copilot_exc) from copilot_exc


@router.post("/conversations/{conversation_id}/confirm", response_model=ExecutionResult)
async def confirm_conversation_plan(
    conversation_id: UUID,
    payload: ConfirmRequest,
    service: OrchestratorService = Depends(get_orchestrator_service),
) -> ExecutionResult:
    """Seller nonce grant, then execute. confirmed=True is never taken from the client."""
    try:
        return await service.confirm(conversation_id, payload)
    except Exception as copilot_exc:
        raise _http_error(copilot_exc) from copilot_exc


@router.post("/synthesize", response_model=SynthesizedResponse)
async def synthesize_evidence(
    payload: SynthesisRequest,
    service: SynthesisService = Depends(get_synthesis_service),
) -> SynthesizedResponse:
    """Ground a seller response in envelopes. Does not plan, execute tools, or chat."""
    try:
        return await service.synthesize(payload)
    except Exception as copilot_exc:
        raise _http_error(copilot_exc) from copilot_exc
