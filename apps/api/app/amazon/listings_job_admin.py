"""12B.3G — Operator-only maintenance entry point for Listings jobs.

No public HTTP endpoint exists for this (deliberately — see the module
docstring on `app.api.routes.amazon_listings_sync`, which stays a
strictly enqueue/status surface). This is a one-off administrative
action, run from a trusted operator's own machine/session against a
specific, already-known run the operator has already identified through
other means (e.g. the read-only monitoring queries in
`docs/AI_HANDOVER/12B3G_DURABLE_LISTINGS_SYNC.md`).

    cd apps/api
    uv run python -m app.amazon.listings_job_admin terminalize-queued \\
        --organization-id <uuid> --run-id <uuid> [--reason cancelled_before_start]

Both `--organization-id` and `--run-id` are required with no default and
no "latest"/"oldest" auto-selection — the operator must supply the exact
job deliberately. Never prints the organization id, run id, marketplace
id, seller identifiers, or any credential to stdout — only a sanitized
outcome. The operator already knows the identifiers they supplied
themselves; this module never echoes them back.

Only ever touches a Listings run that has genuinely never been claimed —
see `AmazonIngestionRunRepository.terminalize_unclaimed_listings_run`'s
own docstring for the exact compare-and-set this relies on to make a
concurrent worker claim and this operator action mutually exclusive.
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from app.persistence.database import session_scope
from app.persistence.repositories import AmazonIngestionRunRepository

_SUCCESS_MESSAGE = "Terminalized: the specified job was queued, unclaimed, and has been marked failed."
_CONFLICT_MESSAGE = (
    "No matching job terminalized. Either it does not exist, belongs to a different "
    "organization, or is no longer in the exact unclaimed-queued state this operation "
    "requires (already claimed by a worker, already terminal, or already changed by "
    "something else since it was last observed)."
)


def terminalize_queued_listings_job(organization_id: UUID, run_id: UUID, *, reason: str) -> bool:
    with session_scope() as session:
        return AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(
            organization_id, run_id, failure_class=reason
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.amazon.listings_job_admin",
        description="Operator-only Listings job maintenance actions. No live Amazon call, ever.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    terminalize = subparsers.add_parser(
        "terminalize-queued",
        help="Terminalize a Listings job that has never been claimed by any worker.",
    )
    terminalize.add_argument(
        "--organization-id", required=True, help="Exact organization id owning the job (required, no default)."
    )
    terminalize.add_argument("--run-id", required=True, help="Exact run id to terminalize (required, no default).")
    terminalize.add_argument(
        "--reason",
        default="cancelled_before_start",
        help="Sanitized failure class recorded on the terminated run (default: cancelled_before_start).",
    )

    args = parser.parse_args(argv)

    try:
        organization_id = UUID(args.organization_id)
        run_id = UUID(args.run_id)
    except ValueError:
        print("Invalid --organization-id or --run-id: both must be valid UUIDs.")
        return 2

    if args.command == "terminalize-queued":
        succeeded = terminalize_queued_listings_job(organization_id, run_id, reason=args.reason)
        print(_SUCCESS_MESSAGE if succeeded else _CONFLICT_MESSAGE)
        return 0 if succeeded else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
