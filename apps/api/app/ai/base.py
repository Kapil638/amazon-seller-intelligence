from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.models.ai_listing_intelligence import AITokenUsage
from app.models.media_evidence import MediaEvidenceItem


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

    async def generate_multimodal_structured(
        self,
        *,
        schema: type[BaseModel],
        system_prompt: str,
        user_prompt: str,
        repair_prompt: str,
        prompt_version: str,
        images: Sequence[MediaEvidenceItem],
    ) -> AIGenerationResult:
        raise NotImplementedError(f"{self.name} does not support multimodal analysis")
