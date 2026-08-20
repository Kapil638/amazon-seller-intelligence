from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.openai_provider import (
    AUTH_MESSAGE,
    MISSING_KEY_MESSAGE,
    MISSING_MODEL_MESSAGE,
    RATE_LIMIT_MESSAGE,
    OpenAIProvider,
)
from app.core.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIRateLimitedError,
    AIRequestFailedError,
    AISafetyRefusalError,
    AIStructuredOutputError,
)
from app.models.ai_listing_intelligence import AIListingIntelligence
from app.prompts.listing_intelligence import PROMPT_VERSION
from tests.test_ai_listing_intelligence import sample_intelligence


class FakeResponses:
    def __init__(self, queue: list[object]) -> None:
        self.queue = list(queue)
        self.calls: list[dict] = []

    async def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, queue: list[object]) -> None:
        self.responses = FakeResponses(queue)


def _usage(input_tokens: int = 11, output_tokens: int = 22, total_tokens: int = 33) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


@pytest.mark.asyncio
async def test_missing_api_key_is_configuration_error() -> None:
    provider = OpenAIProvider(api_key="", model="gpt-5.4", client=FakeClient([]))
    with pytest.raises(AIConfigurationError, match="not configured"):
        await provider.generate_structured(
            schema=AIListingIntelligence,
            system_prompt="sys",
            user_prompt="user",
            repair_prompt="repair",
            prompt_version=PROMPT_VERSION,
        )
    assert MISSING_KEY_MESSAGE


@pytest.mark.asyncio
async def test_missing_model_is_configuration_error() -> None:
    provider = OpenAIProvider(api_key="test-openai-key", model=" ", client=FakeClient([]))
    with pytest.raises(AIConfigurationError, match="OPENAI_MODEL"):
        await provider.generate_structured(
            schema=AIListingIntelligence,
            system_prompt="sys",
            user_prompt="user",
            repair_prompt="repair",
            prompt_version=PROMPT_VERSION,
        )
    assert MISSING_MODEL_MESSAGE


@pytest.mark.asyncio
async def test_auth_failure_is_mapped() -> None:
    class AuthFailure(Exception):
        status_code = 401

    client = FakeClient([AuthFailure("nope")])
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    with pytest.raises(AIAuthenticationError, match="authentication"):
        await provider.generate_structured(
            schema=AIListingIntelligence,
            system_prompt="sys",
            user_prompt="user",
            repair_prompt="repair",
            prompt_version=PROMPT_VERSION,
        )
    assert AUTH_MESSAGE
    assert "test-openai-key" not in str(AIAuthenticationError())


@pytest.mark.asyncio
async def test_rate_limit_is_mapped() -> None:
    class RateFailure(Exception):
        status_code = 429

    client = FakeClient([RateFailure("slow")])
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    with pytest.raises(AIRateLimitedError, match="rate-limited"):
        await provider.generate_structured(
            schema=AIListingIntelligence,
            system_prompt="sys",
            user_prompt="user",
            repair_prompt="repair",
            prompt_version=PROMPT_VERSION,
        )
    assert RATE_LIMIT_MESSAGE


@pytest.mark.asyncio
async def test_network_error_is_mapped() -> None:
    client = FakeClient([Exception("connection reset")])
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    with pytest.raises(AIRequestFailedError, match="could not be completed"):
        await provider.generate_structured(
            schema=AIListingIntelligence,
            system_prompt="sys",
            user_prompt="user",
            repair_prompt="repair",
            prompt_version=PROMPT_VERSION,
        )


@pytest.mark.asyncio
async def test_malformed_response_retries_once_then_succeeds() -> None:
    payload = sample_intelligence()
    client = FakeClient(
        [
            SimpleNamespace(output_parsed=None, usage=_usage(), incomplete_details=None),
            SimpleNamespace(output_parsed=payload, usage=_usage(), incomplete_details=None),
        ]
    )
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    result = await provider.generate_structured(
        schema=AIListingIntelligence,
        system_prompt="sys",
        user_prompt="user",
        repair_prompt="repair",
        prompt_version=PROMPT_VERSION,
    )
    assert len(client.responses.calls) == 2
    assert result.repaired is True
    assert result.payload.executive_summary == payload.executive_summary
    assert result.usage is not None
    assert result.usage.total_tokens == 33


@pytest.mark.asyncio
async def test_malformed_response_retries_at_most_once() -> None:
    client = FakeClient(
        [
            SimpleNamespace(output_parsed=None, usage=None, incomplete_details=None),
            SimpleNamespace(output_parsed=None, usage=None, incomplete_details=None),
        ]
    )
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    with pytest.raises(AIStructuredOutputError):
        await provider.generate_structured(
            schema=AIListingIntelligence,
            system_prompt="sys",
            user_prompt="user",
            repair_prompt="repair",
            prompt_version=PROMPT_VERSION,
        )
    assert len(client.responses.calls) == 2


@pytest.mark.asyncio
async def test_safety_refusal_is_controlled() -> None:
    client = FakeClient(
        [
            SimpleNamespace(
                output_parsed=None,
                usage=None,
                incomplete_details=SimpleNamespace(reason="content_filter"),
            )
        ]
    )
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    with pytest.raises(AISafetyRefusalError):
        await provider.generate_structured(
            schema=AIListingIntelligence,
            system_prompt="sys",
            user_prompt="user",
            repair_prompt="repair",
            prompt_version=PROMPT_VERSION,
        )


def test_invalid_priority_is_rejected() -> None:
    payload = sample_intelligence().model_dump()
    payload["priority_actions"][0]["priority"] = "urgent"
    with pytest.raises(ValidationError):
        AIListingIntelligence.model_validate(payload)


@pytest.mark.asyncio
async def test_competitive_schema_retries_once_then_succeeds() -> None:
    from app.models.ai_competitive_intelligence import AICompetitiveIntelligence
    from tests.test_ai_competitive_intelligence import sample_competitive_intelligence

    payload = sample_competitive_intelligence()
    client = FakeClient(
        [
            SimpleNamespace(output_parsed=None, usage=_usage(), incomplete_details=None),
            SimpleNamespace(output_parsed=payload, usage=_usage(), incomplete_details=None),
        ]
    )
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    result = await provider.generate_structured(
        schema=AICompetitiveIntelligence,
        system_prompt="sys",
        user_prompt="user",
        repair_prompt="repair",
        prompt_version="competitive-intelligence-v1",
    )
    assert len(client.responses.calls) == 2
    assert result.repaired is True
    assert result.payload.executive_summary == payload.executive_summary
    assert result.prompt_version == "competitive-intelligence-v1"


@pytest.mark.asyncio
async def test_competitive_schema_retries_at_most_once() -> None:
    from app.models.ai_competitive_intelligence import AICompetitiveIntelligence

    client = FakeClient(
        [
            SimpleNamespace(output_parsed=None, usage=None, incomplete_details=None),
            SimpleNamespace(output_parsed=None, usage=None, incomplete_details=None),
        ]
    )
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    with pytest.raises(AIStructuredOutputError):
        await provider.generate_structured(
            schema=AICompetitiveIntelligence,
            system_prompt="sys",
            user_prompt="user",
            repair_prompt="repair",
            prompt_version="competitive-intelligence-v1",
        )
    assert len(client.responses.calls) == 2


@pytest.mark.asyncio
async def test_multimodal_structured_sends_image_urls_and_uses_vision_model() -> None:
    from app.models.ai_image_intelligence import AIImageIntelligence
    from app.models.media_evidence import MediaEvidenceItem, MediaSourceType
    from tests.test_ai_image_intelligence import sample_image_intelligence

    payload = sample_image_intelligence()
    client = FakeClient(
        [SimpleNamespace(output_parsed=payload, usage=_usage(), incomplete_details=None)]
    )
    provider = OpenAIProvider(
        api_key="test-openai-key",
        model="gpt-5.4",
        vision_model="gpt-5.4-vision-test",
        client=client,
    )
    images = [
        MediaEvidenceItem(
            id="img-main-1",
            source_type=MediaSourceType.MAIN_IMAGE,
            url="https://m.media-amazon.com/images/I/71kM3BRnDaL.jpg",
            position=0,
        )
    ]
    result = await provider.generate_multimodal_structured(
        schema=AIImageIntelligence,
        system_prompt="sys",
        user_prompt="user",
        repair_prompt="repair",
        prompt_version="image-intelligence-v1",
        images=images,
    )
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.4-vision-test"
    content = call["input"][1]["content"]
    assert content[0]["type"] == "input_text"
    assert any(part.get("type") == "input_image" for part in content)
    assert any(
        part.get("image_url") == "https://m.media-amazon.com/images/I/71kM3BRnDaL.jpg" for part in content
    )
    assert result.model == "gpt-5.4-vision-test"
    assert result.payload.executive_assessment == payload.executive_assessment


@pytest.mark.asyncio
async def test_text_structured_still_uses_openai_model_not_vision_override() -> None:
    client = FakeClient(
        [SimpleNamespace(output_parsed=sample_intelligence(), usage=_usage(), incomplete_details=None)]
    )
    provider = OpenAIProvider(
        api_key="test-openai-key",
        model="gpt-5.4",
        vision_model="gpt-5.4-vision-test",
        client=client,
    )
    await provider.generate_structured(
        schema=AIListingIntelligence,
        system_prompt="sys",
        user_prompt="user",
        repair_prompt="repair",
        prompt_version=PROMPT_VERSION,
    )
    assert client.responses.calls[0]["model"] == "gpt-5.4"
    assert isinstance(client.responses.calls[0]["input"][1]["content"], str)
