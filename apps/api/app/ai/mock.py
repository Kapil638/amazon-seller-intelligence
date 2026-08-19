from datetime import UTC, datetime

from pydantic import BaseModel

from app.ai.base import AIGenerationResult, AIProvider
from app.models.ai_listing_intelligence import AIListingIntelligence, AITokenUsage

MOCK_AI_MODEL = "mock-listing-intelligence-v1"


class MockAIProvider(AIProvider):
    """Deterministic structured listing intelligence. Never calls OpenAI."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return MOCK_AI_MODEL

    def __repr__(self) -> str:
        return "MockAIProvider()"

    async def generate_structured(
        self,
        *,
        schema: type[BaseModel],
        system_prompt: str,
        user_prompt: str,
        repair_prompt: str,
        prompt_version: str,
    ) -> AIGenerationResult:
        if schema is not AIListingIntelligence:
            raise TypeError("MockAIProvider only supports AIListingIntelligence")
        self.calls.append(
            {
                "schema": schema,
                "prompt_version": prompt_version,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "repair_prompt": repair_prompt,
            }
        )
        payload = _fixture_intelligence()
        return AIGenerationResult(
            payload=payload,
            provider=self.name,
            model=self.model,
            prompt_version=prompt_version,
            usage=AITokenUsage(input_tokens=0, output_tokens=0, total_tokens=0),
            latency_ms=1,
            repaired=False,
        )


def _fixture_intelligence() -> AIListingIntelligence:
    return AIListingIntelligence.model_validate(
        {
            "executive_summary": (
                "Mock AI review: this listing has deterministic findings that the seller "
                "should address in priority order. No live model was used."
            ),
            "strengths": ["The product identity is present in the normalized listing."],
            "weaknesses": ["Mock analysis highlights the deterministic findings already scored."],
            "priority_actions": [
                {
                    "priority": "high",
                    "title": "Fix the highest-severity listing gap",
                    "reason": "Deterministic analysis already flagged the most urgent listing issue.",
                    "recommended_action": "Apply the deterministic recommendation before rewriting copy.",
                }
            ],
            "title_recommendation": {
                "current_title": "Current title from the listing",
                "suggested_title": "Clearer title that stays inside known product facts",
                "rationale": "Mock suggestion only uses the existing listing facts.",
            },
            "bullet_recommendations": [
                {
                    "current": "Current bullet",
                    "suggested": "Rewritten bullet using only known listing facts",
                    "rationale": "Keep each bullet to a distinct listed benefit.",
                }
            ],
            "positioning_opportunities": ["Lead with the strongest already-stated benefit."],
            "conversion_opportunities": ["Make missing listing fields complete before testing copy."],
            "risks_and_cautions": ["This is mock AI output and must not be treated as a live model review."],
            "seller_action_plan": [
                {
                    "step": 1,
                    "action": "Resolve high-severity deterministic findings first.",
                    "reason": "Scores and findings come from ListingAnalysis, not from this mock.",
                }
            ],
        }
    )


def mock_ai_generated_at() -> datetime:
    return datetime.now(UTC)
