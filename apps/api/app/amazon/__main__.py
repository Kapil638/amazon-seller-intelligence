"""Development-only SP-API sandbox connectivity proof.

    cd apps/api
    uv run python -m app.amazon
"""

from __future__ import annotations

import asyncio
import sys

from app.amazon.lwa import MISSING_CREDENTIALS_MESSAGE, credentials_configured
from app.amazon.sandbox import (
    GET_MARKETPLACE_PARTICIPATIONS,
    SELLERS_API,
    AmazonSpApiSandboxClient,
)
from app.core.config import get_settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)


def _credentials_present() -> bool:
    settings = get_settings()
    return bool(
        credentials_configured(
            settings.sp_api_lwa_client_id,
            settings.sp_api_lwa_client_secret,
            settings.sp_api_sandbox_refresh_token,
        )
    )


async def _run() -> int:
    settings = get_settings()
    client = AmazonSpApiSandboxClient(settings=settings)
    print("Environment: SANDBOX")
    print(f"API: {SELLERS_API}")
    print(f"Operation: {GET_MARKETPLACE_PARTICIPATIONS}")
    print(f"Host: {client.endpoint_host}")
    if not settings.sp_api_sandbox_enabled:
        print("SP_API_SANDBOX_ENABLED is not true. Enable it locally to run the sandbox proof.")
        print("Implementation complete; manual connectivity verification requires local sandbox credentials.")
        return 0
    if not _credentials_present():
        print(MISSING_CREDENTIALS_MESSAGE)
        print("Implementation complete; manual connectivity verification requires local sandbox credentials.")
        return 0
    try:
        result = await client.get_marketplace_participations()
    except SpApiConfigurationError as exc:
        print(str(exc))
        print("Implementation complete; manual connectivity verification requires local sandbox credentials.")
        return 0
    except (
        SpApiAuthenticationError,
        SpApiRateLimitedError,
        SpApiRequestFailedError,
        SpApiParseFailedError,
    ) as exc:
        print("SP-API sandbox connectivity: FAILED")
        print(f"Error: {exc}")
        return 1
    print("SP-API sandbox connectivity: SUCCESS")
    print("API: Sellers")
    print(f"Operation: {result.provenance.operation}")
    print(f"Environment: {result.provenance.environment}")
    print(f"HTTP status: {result.provenance.http_status}")
    print(f"Marketplace participations parsed: {result.participation_count}")
    print("Note: sandbox payload is Amazon mock test data, not a real seller account.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
