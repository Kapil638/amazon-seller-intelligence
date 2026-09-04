"""Evidence-only synthesis. Does not plan, execute tools, or read the database."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from app.copilot.skills.cache import AsyncSingleFlight, InProcessSkillCache, answer_cache_key, evidence_content_key
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

# 12B.5B — Layer B (final-answer cache), process-wide for this API
# process. See `app/copilot/skills/cache.py`'s module docstring for the
# multi-replica limitation.
#
# 12B.5B remediation: request coalescing across concurrent identical
# cold requests (`AsyncSingleFlight`) — the token-saving goal is
# specifically avoiding two callers both invoking a real attached LLM
# for the same question, and an earlier pass left that case
# uncoalesced. `AsyncSingleFlight` is Layer A's `SingleFlight` reworked
# for a coroutine caller: its follower path awaits an `asyncio.Future`
# instead of blocking on `threading.Event.wait()`, which would have
# frozen the whole event loop for every request this process is
# serving. See `AsyncSingleFlight`'s own docstring in `cache.py`.
_ANSWER_CACHE = InProcessSkillCache()
_ANSWER_CACHE_TTL_SECONDS = 120
_ANSWER_SINGLE_FLIGHT = AsyncSingleFlight()


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

        # 12B.5B Layer B — only ever keyed when a skill_evidence fact is
        # present (the five launch skills' own contract); every other
        # existing intent (explain_profit, explain_listing_score, ...) is
        # unaffected and never cached (or coalesced) here, matching this
        # milestone's explicit "only these five skills" scope.
        cache_key = self._answer_cache_key(facts, intent)
        if cache_key is not None:
            try:
                cached = _ANSWER_CACHE.get(cache_key)
            except Exception:  # noqa: BLE001 - a broken cache must never break a skill
                cached = None
            if cached is not None:
                return cached

            async def _compute_and_store() -> SynthesizedResponse:
                proposal, model_id = await self._propose(user_message, intent, compact, facts)
                if proposal is None:
                    response = template_response(facts, intent=intent, user_message=user_message)
                else:
                    response = validate_proposal(
                        proposal,
                        facts=facts,
                        intent=intent,
                        user_message=user_message,
                        prompt_version=PROMPT_VERSION,
                        synthesis_model=model_id,
                    )
                # Only ever reached on a successful `compute()` return —
                # an exception here propagates out of `AsyncSingleFlight.
                # run()` instead, so a failure is never cached.
                try:
                    _ANSWER_CACHE.set(cache_key, response, _ANSWER_CACHE_TTL_SECONDS)
                except Exception:  # noqa: BLE001 - a broken cache must never break a skill
                    pass
                return response

            return await _ANSWER_SINGLE_FLIGHT.run(cache_key, _compute_and_store)

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

    def _answer_cache_key(self, facts, intent: str) -> str | None:
        skill_fact = next((item for item in facts if item.claim_key == "skill_evidence"), None)
        if skill_fact is None or not isinstance(skill_fact.value, dict):
            return None
        evidence_key = evidence_content_key(skill_fact.value)
        provider_name = type(self._generator).__name__ if self._generator is not None else "template_fallback"
        return answer_cache_key(
            evidence_key=evidence_key,
            intent=intent,
            prompt_version=PROMPT_VERSION,
            model=None,
            provider=provider_name,
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
