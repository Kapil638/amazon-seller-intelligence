"""Evidence-only synthesis. Does not plan, execute tools, or read the database."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from app.copilot.synthesis.prompts import REPAIR_PROMPT, SYSTEM_PROMPT, build_user_prompt
from app.copilot.synthesis.schemas import (
    PROMPT_VERSION,
    SynthesisProposal,
    SynthesisRequest,
    SynthesizedResponse,
)
from app.copilot.synthesis.validator import (
    build_allowed_facts,
    copy_evidence,
    sanitize_compact_context,
    template_response,
    validate_proposal,
)
from app.persistence.database import sqlalchemy_database_url


class SynthesisGenerator(Protocol):
    """Optional language model. Failures must fall back to the evidence template."""

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        repair_prompt: str,
        prompt_version: str,
    ) -> tuple[SynthesisProposal | None, str | None]: ...


class AIProviderSynthesizer:
    """One structured generate_structured call. Never executes Copilot tools."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        repair_prompt: str,
        prompt_version: str,
    ) -> tuple[SynthesisProposal | None, str | None]:
        result = await self._provider.generate_structured(
            schema=SynthesisProposal,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            repair_prompt=repair_prompt,
            prompt_version=prompt_version,
        )
        payload = result.payload
        model_id = getattr(self._provider, "model", None)
        if isinstance(payload, SynthesisProposal):
            return payload, model_id
        try:
            return SynthesisProposal.model_validate(payload), model_id
        except ValidationError:
            return None, model_id


class SynthesisService:
    """allowed_facts → optional LLM draft → citation validator → seller response."""

    def __init__(self, *, generator: SynthesisGenerator | None = None) -> None:
        self._generator = generator

    async def synthesize(self, request: SynthesisRequest) -> SynthesizedResponse:
        evidence = copy_evidence(request.evidence)
        compact = sanitize_compact_context(request.compact_context)
        facts = build_allowed_facts(evidence)
        intent = (request.intent or "").strip() or "explain_listing_score"
        user_message = request.user_message

        if intent == "out_of_scope":
            return template_response(facts, intent=intent, user_message=user_message)
        if intent == "clarify" and not facts:
            return template_response(facts, intent=intent, user_message=user_message)

        proposal, model_id = await self._propose(user_message, intent, compact, facts)
        if proposal is None:
            return template_response(facts, intent=intent, user_message=user_message)
        return validate_proposal(
            proposal,
            facts=facts,
            intent=intent,
            user_message=user_message,
            prompt_version=PROMPT_VERSION,
            synthesis_model=model_id,
        )

    async def _propose(
        self,
        user_message: str,
        intent: str,
        compact: dict,
        facts,
    ) -> tuple[SynthesisProposal | None, str | None]:
        if self._generator is None:
            return None, None
        try:
            proposal, model_id = await self._generator.generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(
                    user_message=user_message,
                    intent=intent,
                    compact_context=compact,
                    allowed_facts=facts,
                ),
                repair_prompt=REPAIR_PROMPT,
                prompt_version=PROMPT_VERSION,
            )
        except Exception:
            return None, None
        if proposal is None:
            return None, model_id
        return proposal, model_id


def _sqlite_test_database() -> bool:
    url = sqlalchemy_database_url()
    return url.startswith("sqlite")


def get_synthesis_service() -> SynthesisService:
    """Production may attach an LLM; SQLite/tests stay on the evidence template."""
    generator: SynthesisGenerator | None = None
    if not _sqlite_test_database():
        try:
            from app.ai.factory import get_ai_provider

            generator = AIProviderSynthesizer(get_ai_provider())
        except Exception:
            generator = None
    return SynthesisService(generator=generator)
