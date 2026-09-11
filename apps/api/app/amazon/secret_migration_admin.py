"""pilot-deployment-ewise, correction 1 — Operator-only, one-off migration
path from the local `DevelopmentSecretProvider` file store into the
production PostgreSQL + AES-256-GCM `SecretProvider` backend
(`app.amazon.production_secrets.ProductionSecretProvider`).

Not run by this milestone against the real local seller token — this
module exists so a future, deliberate operator invocation follows a
known, reviewed procedure instead of an improvised one. It never
enumerates or migrates every secret in a store, and never migrates
anything as a side effect of an import, a deploy, or a service start —
only an explicit `migrate --reference <ref>` invocation touches
anything.

    cd apps/api
    AMAZON_SECRET_BACKEND=production \\
    AMAZON_SECRET_ENCRYPTION_KEYS='{"v1":"<base64 32-byte key>"}' \\
    AMAZON_SECRET_ACTIVE_KEY_VERSION=v1 \\
    uv run python -m app.amazon.secret_migration_admin migrate \\
        --reference asi/amazon/SP_API/PRODUCTION/<organization_id>/<connection_id> \\
        --development-store .data/amazon-development-secrets.json

`--dry-run` verifies the source value exists and is readable without
writing anything to the production backend — use this first. Never
prints the migrated value on any path, success or failure; only the
(non-secret, already-validated) reference string and a fixed outcome
message are ever printed.
"""

from __future__ import annotations

import argparse
import os
import sys

from app.amazon.secrets import (
    DevelopmentSecretProvider,
    InvalidSecretReferenceError,
    SecretAccessError,
    SecretProviderFactory,
    validate_secret_reference,
)
from app.core.config import get_settings

DRY_RUN_FOUND_MESSAGE = "Source value found and readable. --dry-run: nothing written."
MIGRATED_MESSAGE = "Migrated: value written to the production backend under the same reference."
NOT_FOUND_MESSAGE = "No value found for that reference in the development store."


def migrate_reference(reference: str, *, development_store: str, dry_run: bool) -> str:
    """Read one value from the DevelopmentSecretProvider file store and
    write it to the currently-configured production backend under the
    same reference. Requires AMAZON_SECRET_BACKEND=production (and valid
    key configuration) in the calling process's own settings — this
    function never selects a backend itself, it uses whatever
    SecretProviderFactory().create() resolves to, so a misconfigured
    environment fails closed exactly as it would for normal application
    code, never silently falling back to development."""
    key = validate_secret_reference(reference)
    source = DevelopmentSecretProvider(store_path=development_store)
    if not source.exists(key):
        return NOT_FOUND_MESSAGE
    value = source.get_secret(key)
    if dry_run:
        return DRY_RUN_FOUND_MESSAGE
    target = SecretProviderFactory().create(get_settings())
    target.put_secret(key, value)
    return MIGRATED_MESSAGE


def main(argv: list[str] | None = None) -> int:
    # Same admin runtime-context declaration as
    # app.amazon.listings_job_admin — see that module's own docstring for
    # why "admin" alone still requires the separate
    # ASI_ALLOW_PRODUCTION_DB_ACCESS override for a real remote database.
    os.environ["ASI_DB_RUNTIME_CONTEXT"] = "admin"

    parser = argparse.ArgumentParser(
        prog="python -m app.amazon.secret_migration_admin",
        description=(
            "Operator-only migration of one Amazon secret reference from the local "
            "DevelopmentSecretProvider file store into the configured production "
            "SecretProvider backend. Never migrates more than one explicitly-named "
            "reference per invocation."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser(
        "migrate", help="Migrate one secret reference from the development file store to production."
    )
    migrate.add_argument(
        "--reference", required=True, help="Exact ASI secret reference to migrate (required, no default)."
    )
    migrate.add_argument(
        "--development-store",
        required=True,
        help="Path to the DevelopmentSecretProvider JSON file store to read from.",
    )
    migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify the source value exists and is readable; write nothing.",
    )

    args = parser.parse_args(argv)

    if args.command == "migrate":
        try:
            message = migrate_reference(
                args.reference, development_store=args.development_store, dry_run=args.dry_run
            )
        except (SecretAccessError, InvalidSecretReferenceError) as exc:
            print(f"Migration failed: {exc}")
            return 1
        print(message)
        print(f"reference: {args.reference}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
