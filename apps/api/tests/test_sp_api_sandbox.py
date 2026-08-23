from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL, LwaClient
from app.amazon.models import LwaTokenResponse
from app.amazon.sandbox import (
    GET_MARKETPLACE_PARTICIPATIONS,
    MARKETPLACE_PARTICIPATIONS_PATH,
    AmazonSpApiSandboxClient,
    sandbox_base_url,
)
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sp_api"
CLIENT_ID = "amzn1.application-oa2-client.test"
CLIENT_SECRET = "test-lwa-client-secret-value"
REFRESH_TOKEN = "Atzr|test-sandbox-refresh-token"
ACCESS_TOKEN = "Atza|test-sandbox-access-token"


def _sandbox_payload() -> dict:
    return json.loads((FIXTURES / "get_marketplace_participations.sandbox.json").read_text(encoding="utf-8"))


def _lwa_success() -> dict:
    return {"access_token": ACCESS_TOKEN, "token_type": "bearer", "expires_in": 3600}


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode("utf-8"))


def _lwa_client(transport: httpx.BaseTransport) -> LwaClient:
    return LwaClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
        transport=transport,
    )


def _sandbox_client(transport: httpx.BaseTransport) -> AmazonSpApiSandboxClient:
    return AmazonSpApiSandboxClient(
        lwa=_lwa_client(transport),
        transport=transport,
        region="eu",
    )


@pytest.mark.asyncio
async def test_lwa_token_request_structure_and_typed_parse() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_lwa_success())

    token = await _lwa_client(httpx.MockTransport(handler)).fetch_access_token()
    assert str(captured[0].url) == DEFAULT_LWA_TOKEN_URL
    form = _form(captured[0])
    assert form["grant_type"] == ["refresh_token"]
    assert form["client_id"] == [CLIENT_ID]
    assert "refresh_token" in form
    assert "client_secret" in form
    assert token.token_type == "bearer"
    assert token.expires_in == 3600
    assert token.access_token.get_secret_value() == ACCESS_TOKEN
    dumped = token.model_dump_json()
    assert ACCESS_TOKEN not in dumped
    assert CLIENT_SECRET not in dumped
    assert REFRESH_TOKEN not in dumped
    assert ACCESS_TOKEN not in repr(token)


@pytest.mark.asyncio
async def test_lwa_authentication_error_timeout_and_malformed() -> None:
    def auth(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "bearer"})

    for handler, expected in (
        (auth, SpApiAuthenticationError),
        (timeout, SpApiRequestFailedError),
        (malformed, SpApiParseFailedError),
    ):
        client = _lwa_client(httpx.MockTransport(handler))
        with pytest.raises(expected) as exc_info:
            await client.fetch_access_token()
        message = str(exc_info.value)
        assert CLIENT_SECRET not in message
        assert REFRESH_TOKEN not in message
        assert ACCESS_TOKEN not in message


@pytest.mark.asyncio
async def test_lwa_missing_credentials() -> None:
    client = LwaClient(client_id="", client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN)
    with pytest.raises(SpApiConfigurationError, match="SP_API_LWA_CLIENT_ID"):
        await client.fetch_access_token()


def test_sandbox_eu_base_url() -> None:
    assert sandbox_base_url("eu") == "https://sandbox.sellingpartnerapi-eu.amazon.com"
    assert sandbox_base_url("eu", "https://sandbox.example.test/") == "https://sandbox.example.test"


@pytest.mark.asyncio
async def test_sandbox_sellers_call_headers_path_and_parse() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json=_lwa_success())
        return httpx.Response(200, json=_sandbox_payload())

    result = await _sandbox_client(httpx.MockTransport(handler)).get_marketplace_participations()
    sellers = captured[1]
    assert sellers.url.host == "sandbox.sellingpartnerapi-eu.amazon.com"
    assert sellers.url.path == MARKETPLACE_PARTICIPATIONS_PATH
    assert sellers.method == "GET"
    assert sellers.headers["x-amz-access-token"] == ACCESS_TOKEN
    assert "x-amz-date" in sellers.headers
    assert result.participation_count == 1
    assert result.payload[0].marketplace.id == "ATVPDKIKX0DER"
    assert result.payload[0].store_name == "BestSellerStore"
    assert result.payload[0].participation.is_participating is True
    assert result.provenance.provider == "amazon_sp_api"
    assert result.provenance.environment == "sandbox"
    assert result.provenance.api == "sellers"
    assert result.provenance.operation == GET_MARKETPLACE_PARTICIPATIONS
    assert result.provenance.region == "eu"
    assert result.provenance.endpoint_host == "sandbox.sellingpartnerapi-eu.amazon.com"
    assert result.provenance.http_status == 200
    assert result.provenance.api_model_version == "sellers-api-model/v1"
    assert result.provenance.fetched_at is not None
    blob = result.model_dump_json()
    assert ACCESS_TOKEN not in blob
    assert CLIENT_SECRET not in blob
    assert REFRESH_TOKEN not in blob
    assert "access_token" not in result.model_dump()
    assert "authorization" not in blob.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, SpApiAuthenticationError),
        (403, SpApiAuthenticationError),
        (429, SpApiRateLimitedError),
        (500, SpApiRequestFailedError),
    ],
)
async def test_sandbox_http_errors(status: int, expected: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json=_lwa_success())
        return httpx.Response(status, json={"errors": [{"code": "error", "message": ACCESS_TOKEN}]})

    with pytest.raises(expected) as exc_info:
        await _sandbox_client(httpx.MockTransport(handler)).get_marketplace_participations()
    assert ACCESS_TOKEN not in str(exc_info.value)
    assert CLIENT_SECRET not in str(exc_info.value)


@pytest.mark.asyncio
async def test_sandbox_timeout_and_malformed_payload() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json=_lwa_success())
        raise httpx.TimeoutException("timed out")

    with pytest.raises(SpApiRequestFailedError, match="timed out"):
        await _sandbox_client(httpx.MockTransport(timeout)).get_marketplace_participations()

    def malformed(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json=_lwa_success())
        return httpx.Response(200, json={"payload": {"not": "a list"}})

    with pytest.raises(SpApiParseFailedError):
        await _sandbox_client(httpx.MockTransport(malformed)).get_marketplace_participations()


def test_lwa_token_response_does_not_serialize_secret() -> None:
    token = LwaTokenResponse(access_token=ACCESS_TOKEN, token_type="bearer", expires_in=3600)
    assert ACCESS_TOKEN not in token.model_dump_json()
    assert ACCESS_TOKEN not in repr(token)


def test_amazon_package_does_not_import_intelligence_layers() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "amazon"
    forbidden = {
        "app.copilot",
        "app.services.profit_modeling_service",
        "app.services.advertising_modeling_service",
        "app.services.listing_analysis_v2_service",
    }
    imported: set[str] = set()
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    for name in forbidden:
        assert name not in imported, name
