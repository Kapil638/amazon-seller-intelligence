"""Amazon Ads report-sync worker entrypoint. 12C read-only foundation.

**Never deployed this iteration** — no fifth Railway service exists or is
created by this file's existence (see the governing task's explicit
constraint and `docs/AI_HANDOVER/21_AMAZON_ADS_READONLY_FOUNDATION.md`'s
deployment-plan section for the resource/connection-budget estimate a
future deployment pass would use).

Mirrors `app.amazon.sales_traffic_worker`'s exact fail-closed shape:
`main()` checks `ASI_ADS_WORKER_ENABLED` before touching the database or
any Amazon endpoint and exits with `EXIT_DISABLED` if unset — the same
mechanism that already makes the four real workers safe to build/deploy
without becoming active claim/poll processes. This file is never
imported by `app.main` (the API process) and is not referenced by any
Railway service configuration.

A second, independent gate sits behind the first: even with
`ASI_ADS_WORKER_ENABLED=true`, `main()` refuses to start (`EXIT_BACKEND_
NOT_HTTP`) unless `Settings.ads_api_backend` resolves to `"http"` (see
`app.amazon.ads_client.build_amazon_ads_api_client`). Both must be
deliberately configured together — the enable flag alone can never
start a worker that talks to anything but the fail-closed
`DisabledAmazonAdsApiClient`, and the backend setting alone can never
start an actual claim/poll loop. Running this file today (from tests;
it is not referenced by any Railway service) with neither set proves
only the disabled path.
"""

from __future__ import annotations

import asyncio
import logging
import os

from pydantic import ValidationError

from app.amazon.ads_client import ADS_API_BACKEND_HTTP, build_amazon_ads_api_client, resolve_ads_api_backend
from app.amazon.ads_report_service import AmazonAdsReportService
from app.amazon.secrets import get_secret_provider
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WORKER_ENABLED_ENV_VAR = "ASI_ADS_WORKER_ENABLED"
_WORKER_ENABLED_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 2
EXIT_DISABLED = 3
EXIT_BACKEND_NOT_HTTP = 4


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

    # Second, independent gate: even with the enable flag set, this
    # worker must never run against anything but the real HTTP backend —
    # a deployed worker actually claiming/processing jobs with
    # ADS_API_BACKEND left at its "disabled" default (or accidentally
    # "mock") would either do nothing useful or silently write fabricated
    # data into production tables. Both this flag and the backend must be
    # deliberately configured together; neither alone is sufficient.
    backend = resolve_ads_api_backend(settings)
    if backend != ADS_API_BACKEND_HTTP:
        logger.error(
            "amazon ads worker is enabled but ADS_API_BACKEND=%r (must be %r to run for real) — "
            "refusing to start (exit code %d)",
            backend,
            ADS_API_BACKEND_HTTP,
            EXIT_BACKEND_NOT_HTTP,
        )
        return EXIT_BACKEND_NOT_HTTP

    service = AmazonAdsReportService(
        settings=settings,
        secret_provider=get_secret_provider(settings),
        ads_client=build_amazon_ads_api_client(settings),
        lease_owner=_default_lease_owner(),
    )

    try:
        asyncio.run(run_forever(service, idle_poll_seconds=settings.ads_report_poll_interval_seconds))
    except KeyboardInterrupt:
        pass
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
