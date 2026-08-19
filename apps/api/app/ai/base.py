from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.models.ai_listing_intelligence import AITokenUsage


class AIGenerationResult(BaseModel):
    payload: Any
    provider: str
    model: str
    prompt_version: str
    usage: AITokenUsage | None = None
    latency_ms: int | None = None
    repaired: bool = False


class AIProvider(ABC):
    """Abstraction over LLM vendors. OpenAI-specific code stays in OpenAIProvider."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    async def generate_structured(
        self,
        *,
        schema: type[BaseModel],
        system_prompt: str,
        user_prompt: str,
        repair_prompt: str,
        prompt_version: str,
    ) -> AIGenerationResult: ...
