"""Amazon Ads report-sync worker entrypoint. 12C read-only foundation.

**Never deployed this iteration** — no fifth Railway service exists or is
created by this file's existence (see the governing task's explicit
constraint and `docs/AI_HANDOVER/22_AMAZON_ADS_READONLY_FOUNDATION.md`'s
deployment-plan section for the resource/connection-budget estimate a
future deployment pass would use).

Mirrors `app.amazon.sales_traffic_worker`'s exact fail-closed shape:
`main()` checks `ASI_ADS_WORKER_ENABLED` before touching the database or
any Amazon endpoint and exits with `EXIT_DISABLED` if unset — the same
mechanism that already makes the four real workers safe to build/deploy
without becoming active claim/poll processes. This file is never
imported by `app.main` (the API process) and is not referenced by any
Railway service configuration; running it manually with the flag unset
(the only way it is ever invoked in this repository today, from tests)
proves the disabled path and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import os

from pydantic import ValidationError

from app.amazon.ads_client import MockAmazonAdsApiClient
from app.amazon.ads_report_service import AmazonAdsReportService
from app.amazon.secrets import get_secret_provider
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WORKER_ENABLED_ENV_VAR = "ASI_ADS_WORKER_ENABLED"
_WORKER_ENABLED_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 2
EXIT_DISABLED = 3


def is_worker_enabled() -> bool:
    return os.environ.get(_WORKER_ENABLED_ENV_VAR, "").strip().lower() in _WORKER_ENABLED_TRUE_VALUES


def _default_lease_owner() -> str:
    return f"ads-worker-{os.getpid()}"


async def run_forever(service: AmazonAdsReportService, *, idle_poll_seconds: float, stop_after: int | None = None) -> None:
    """Claim-and-process loop. `stop_after` (test-only) bounds the number
    of claim attempts so a test never spins forever waiting for a job
    that will never arrive."""
    iterations = 0
    while stop_after is None or iterations < stop_after:
        outcome = await service.process_one_claimed_job()
        if outcome.outcome == "no_job":
            await asyncio.sleep(idle_poll_seconds)
        iterations += 1


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    if not is_worker_enabled():
        logger.error(
            "amazon ads worker is disabled — set %s=true to allow this process to claim and "
            "process report jobs (refusing to start, exit code %d)",
            _WORKER_ENABLED_ENV_VAR,
            EXIT_DISABLED,
        )
        return EXIT_DISABLED

    # Reached only after the enable-flag check above — see
    # `sales_traffic_worker.main`'s identical comment on why this is
    # never set unconditionally at import time.
    os.environ["ASI_DB_RUNTIME_CONTEXT"] = "ads_worker"

    try:
        settings = get_settings()
    except ValidationError:
        logger.error(
            "amazon ads worker configuration is invalid; refusing to start (exit code %d)",
            EXIT_CONFIGURATION_ERROR,
        )
        return EXIT_CONFIGURATION_ERROR

    # `MockAmazonAdsApiClient` here is deliberate, not a placeholder bug:
    # this file is never enabled in any deployed environment this
    # iteration (see module docstring), so there is no real
    # `HttpAmazonAdsApiClient` wiring to inject yet. The post-approval
    # deployment pass must replace this before ever setting
    # ASI_ADS_WORKER_ENABLED=true anywhere real.
    service = AmazonAdsReportService(
        settings=settings,
        secret_provider=get_secret_provider(settings),
        ads_client=MockAmazonAdsApiClient(),
        lease_owner=_default_lease_owner(),
    )

    try:
        asyncio.run(run_forever(service, idle_poll_seconds=settings.ads_report_poll_interval_seconds))
    except KeyboardInterrupt:
        pass
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
