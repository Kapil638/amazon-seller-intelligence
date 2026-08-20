from datetime import UTC, datetime
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ai.base import AIGenerationResult, AIProvider
from app.core.config import get_settings
from app.core.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIRateLimitedError,
    AIRequestFailedError,
    AISafetyRefusalError,
    AIStructuredOutputError,
)
from app.models.ai_listing_intelligence import AITokenUsage
from app.models.media_evidence import MediaEvidenceItem

MISSING_KEY_MESSAGE = "AI analysis is not configured."
MISSING_MODEL_MESSAGE = "OPENAI_MODEL is not configured."
AUTH_MESSAGE = "AI provider authentication failed."
RATE_LIMIT_MESSAGE = "AI service is temporarily rate-limited or quota-limited."
TIMEOUT_MESSAGE = "AI analysis timed out."
NETWORK_MESSAGE = "Could not reach the AI provider."
STRUCTURED_MESSAGE = "AI returned an unusable structured response."
REFUSAL_MESSAGE = "AI declined to analyze this listing."


class OpenAIProvider(AIProvider):
    """OpenAI Responses API + structured output. The only module that imports the OpenAI SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        timeout_seconds: float | None = None,
        max_output_tokens: int | None = None,
        vision_model: str | None = None,
    ) -> None:
        settings = get_settings()
        if api_key is None:
            secret = settings.openai_api_key
            api_key = secret.get_secret_value() if secret is not None else ""
        if model is None:
            model = settings.openai_model
        if vision_model is None:
            vision_model = settings.openai_vision_model
        self._api_key = (api_key or "").strip()
        self._model = (model or "").strip()
        self._vision_model = (vision_model or "").strip() or self._model
        self._timeout = timeout_seconds if timeout_seconds is not None else settings.openai_timeout_seconds
        self._max_output_tokens = (
            max_output_tokens if max_output_tokens is not None else settings.openai_max_output_tokens
        )
        self._client = client

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    @property
    def vision_model(self) -> str:
        return self._vision_model

    def __repr__(self) -> str:
        return "OpenAIProvider()"

    def _ensure_configured(self) -> None:
        if not self._api_key:
            raise AIConfigurationError(MISSING_KEY_MESSAGE)
        if not self._model:
            raise AIConfigurationError(MISSING_MODEL_MESSAGE)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=self._api_key, timeout=self._timeout)
        return self._client

    async def generate_structured(
        self,
        *,
        schema: type[BaseModel],
        system_prompt: str,
        user_prompt: str,
        repair_prompt: str,
        prompt_version: str,
    ) -> AIGenerationResult:
        self._ensure_configured()
        started = datetime.now(UTC)
        last_structured_error: Exception | None = None
        usage: AITokenUsage | None = None
        repaired = False

        for attempt in (1, 2):
            prompt = user_prompt if attempt == 1 else repair_prompt
            repaired = attempt == 2
            try:
                response = await self._get_client().responses.parse(
                    model=self._model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    text_format=schema,
                    max_output_tokens=self._max_output_tokens,
                )
            except Exception as exc:
                self._raise_openai_error(exc)

            usage = _usage_from_response(response)
            refusal = _refusal_reason(response)
            if refusal:
                raise AISafetyRefusalError(REFUSAL_MESSAGE)

            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                last_structured_error = AIStructuredOutputError(STRUCTURED_MESSAGE)
                continue
            try:
                payload = _validate_payload(parsed, schema)
            except ValidationError:
                last_structured_error = AIStructuredOutputError(STRUCTURED_MESSAGE)
                continue

            latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            return AIGenerationResult(
                payload=payload,
                provider=self.name,
                model=self._model,
                prompt_version=prompt_version,
                usage=usage,
                latency_ms=latency_ms,
                repaired=repaired,
            )

        raise last_structured_error or AIStructuredOutputError(STRUCTURED_MESSAGE)

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
        self._ensure_configured()
        if not self._vision_model:
            raise AIConfigurationError(MISSING_MODEL_MESSAGE)
        if not images:
            raise AIRequestFailedError("No images were supplied for multimodal analysis.")
        started = datetime.now(UTC)
        last_structured_error: Exception | None = None
        usage: AITokenUsage | None = None
        repaired = False
        labeled = list(images)

        for attempt in (1, 2):
            prompt = user_prompt if attempt == 1 else repair_prompt
            repaired = attempt == 2
            try:
                response = await self._get_client().responses.parse(
                    model=self._vision_model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": _multimodal_user_content(prompt, labeled),
                        },
                    ],
                    text_format=schema,
                    max_output_tokens=self._max_output_tokens,
                )
            except Exception as exc:
                self._raise_openai_error(exc)

            usage = _usage_from_response(response)
            refusal = _refusal_reason(response)
            if refusal:
                raise AISafetyRefusalError(REFUSAL_MESSAGE)

            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                last_structured_error = AIStructuredOutputError(STRUCTURED_MESSAGE)
                continue
            try:
                payload = _validate_payload(parsed, schema)
            except ValidationError:
                last_structured_error = AIStructuredOutputError(STRUCTURED_MESSAGE)
                continue

            latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            return AIGenerationResult(
                payload=payload,
                provider=self.name,
                model=self._vision_model,
                prompt_version=prompt_version,
                usage=usage,
                latency_ms=latency_ms,
                repaired=repaired,
            )

        raise last_structured_error or AIStructuredOutputError(STRUCTURED_MESSAGE)

    def _raise_openai_error(self, exc: Exception) -> None:
        import openai

        if isinstance(exc, openai.AuthenticationError):
            raise AIAuthenticationError(AUTH_MESSAGE) from None
        if isinstance(exc, openai.RateLimitError):
            raise AIRateLimitedError(RATE_LIMIT_MESSAGE) from None
        if isinstance(exc, openai.APITimeoutError):
            raise AIRequestFailedError(TIMEOUT_MESSAGE) from None
        if isinstance(exc, openai.APIConnectionError):
            raise AIRequestFailedError(NETWORK_MESSAGE) from None
        status = getattr(exc, "status_code", None)
        if status == 401:
            raise AIAuthenticationError(AUTH_MESSAGE) from None
        if status in {429, 402}:
            raise AIRateLimitedError(RATE_LIMIT_MESSAGE) from None
        raise AIRequestFailedError("AI analysis could not be completed.") from None


_SOURCE_LABELS = {
    "main_image": "MAIN IMAGE",
    "gallery": "GALLERY IMAGE",
    "a_plus": "A+ IMAGE",
    "brand_story": "BRAND STORY IMAGE",
}


def _multimodal_user_content(prompt: str, images: Sequence[MediaEvidenceItem]) -> list[dict[str, str]]:
    content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
    for item in images:
        label = _SOURCE_LABELS.get(item.source_type.value, item.source_type.value.upper())
        content.append(
            {
                "type": "input_text",
                "text": f"BEGIN UNTRUSTED IMAGE {item.id} ({label})",
            }
        )
        content.append(
            {
                "type": "input_image",
                "image_url": item.url,
            }
        )
        content.append({"type": "input_text", "text": f"END UNTRUSTED IMAGE {item.id}"})
    return content


def _validate_payload(parsed: Any, schema: type[BaseModel]) -> BaseModel:
    if isinstance(parsed, schema):
        return parsed
    if isinstance(parsed, BaseModel):
        return schema.model_validate(parsed.model_dump())
    return schema.model_validate(parsed)


def _usage_from_response(response: Any) -> AITokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        details = usage.get("input_tokens_details")
    else:
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        details = getattr(usage, "input_tokens_details", None)
    cached_input_tokens = _cached_input_tokens(details)
    if input_tokens is None and output_tokens is None and total_tokens is None and cached_input_tokens is None:
        return None
    return AITokenUsage(
        input_tokens=input_tokens if isinstance(input_tokens, int) else None,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens if isinstance(output_tokens, int) else None,
        total_tokens=total_tokens if isinstance(total_tokens, int) else None,
    )


def _cached_input_tokens(details: Any) -> int | None:
    if details is None:
        return None
    if isinstance(details, dict):
        value = details.get("cached_tokens", details.get("cached_input_tokens"))
    else:
        value = getattr(details, "cached_tokens", None)
        if value is None:
            value = getattr(details, "cached_input_tokens", None)
    return value if isinstance(value, int) else None


def _refusal_reason(response: Any) -> str | None:
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None) if details is not None else None
    if reason in {"content_filter", "refusal"}:
        return str(reason)
    return None
