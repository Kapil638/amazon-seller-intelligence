from functools import lru_cache

from app.ai.base import AIProvider
from app.ai.openai_provider import OpenAIProvider
from app.core.config import get_settings


@lru_cache
def get_ai_provider() -> AIProvider:
    settings = get_settings()
    if settings.ai_provider == "openai":
        return OpenAIProvider()
    raise ValueError(f"Unknown AI provider: {settings.ai_provider}")
