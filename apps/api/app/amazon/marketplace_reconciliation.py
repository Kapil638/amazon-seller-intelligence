"""12B.2B — Canonical marketplace-participation reconciliation.

Takes an already-fetched, already-validated `SellerValidationResult` and
reconciles its normalized participations into the canonical
`amazon_seller_accounts` / `amazon_marketplace_participations` tables,
recording lifecycle via `amazon_ingestion_runs`. Makes no SP-API calls of
its own, stores no tokens/secrets, and never touches `amazon_connections`
(that remains `AmazonConnectionService`'s responsibility).

Each phase — start run, reconcile, complete run — uses its own
`session_scope()`. A session that raised is never reused: on failure the
next phase always opens a fresh session against a fresh connection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.amazon.seller_validation import NormalizedMarketplaceParticipation
from app.persistence.database import session_scope
from app.persistence.repositories import (
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
    SellerAccountOwnershipConflict,
)

logger = logging.getLogger(__name__)

INGESTION_DOMAIN = "sellers_marketplace_participations"

ReconciliationFailureReason = str  # "identity_missing" | "empty_snapshot" | "malformed_participations" | "ownership_conflict" | "database_failure"


@dataclass(frozen=True)
class ReconciliationOutcome:
    """Result of one reconciliation attempt. Never carries tokens or raw payloads."""

    succeeded: bool
    seller_account_id: UUID | None = None
    reason: ReconciliationFailureReason | None = None
    ingestion_run_id: UUID | None = None
    records_received: int = 0
    records_accepted: int = 0
    records_rejected: int = 0


def _deterministic_display_store_name(
    participations: list[NormalizedMarketplaceParticipation],
) -> str | None:
    """Pick one seller-account-level display name deterministically.

    Never "last row wins": Amazon's payload order is not a stable identity.
    Prefer the store name from the participating marketplace with the
    lowest marketplace id; fall back to the lowest marketplace id with any
    store name at all. Per-marketplace `store_name` is preserved separately
    on every participation row regardless of this choice.
    """
    participating_named = sorted(
        (p for p in participations if p.is_participating and p.store_name),
        key=lambda p: p.marketplace_id,
    )
    if participating_named:
        return participating_named[0].store_name
    any_named = sorted((p for p in participations if p.store_name), key=lambda p: p.marketplace_id)
    if any_named:
        return any_named[0].store_name
    return None


class AmazonMarketplaceReconciliationService:
    """Reconciles a validated Sellers snapshot into canonical seller-identity rows."""

    def __repr__(self) -> str:
        return "AmazonMarketplaceReconciliationService()"

    def reconcile(
        self,
        *,
        organization_id: UUID,
        connection_id: UUID,
        region: str,
        environment: str,
        selling_partner_id: str,
        participations: list[NormalizedMarketplaceParticipation],
    ) -> ReconciliationOutcome:
        spid = (selling_partner_id or "").strip()
        if not spid:
            # No canonical seller account can be scoped without an identity
            # at all, so there is nothing meaningful for an ingestion run to
            # bind to — this stays a connection-level fail-closed state,
            # handled upstream in AmazonSellerValidationService.validate /
            # AmazonConnectionService._apply_seller_validation, and is
            # unreachable from that real call chain. Kept here only as a
            # defensive guard for direct callers.
            logger.info(
                "amazon marketplace reconciliation rejected reason=identity_missing connection_id=%s",
                connection_id,
            )
            return ReconciliationOutcome(succeeded=False, reason="identity_missing")

        records_received = len(participations)
        valid_participations = [p for p in participations if (p.marketplace_id or "").strip()]
        rejected_count = records_received - len(valid_participations)

        # Decide up front whether this snapshot can be trusted at all, before
        # ever starting an ingestion run, so the run — once started — always
        # records a truthful, final classification.
        #
        # An empty payload (zero entries of any kind) is distinguished from a
        # malformed one (entries present but unusable): existing product
        # behavior already treats zero participating marketplaces as a
        # validation failure (`seller_identity_unavailable` in
        # AmazonSellerValidationService.validate, which gates reconcile()
        # from ever being reached with an empty payload on the real
        # handshake path), so an empty snapshot here is never treated as "the
        # seller now has no marketplaces" — it is rejected outright rather
        # than risk deactivating every previously-known marketplace on
        # what is far more likely a transport/parsing artifact than a real
        # business fact.
        #
        # A snapshot mixing valid and malformed entries is rejected in full,
        # not partially reconciled: a malformed entry is not evidence the
        # seller left that marketplace, and Amazon's contract gives no basis
        # for treating a partially-unusable response as a complete,
        # authoritative absence signal for deactivation purposes.
        snapshot_failure_reason: ReconciliationFailureReason | None = None
        if records_received == 0:
            snapshot_failure_reason = "empty_snapshot"
        elif rejected_count > 0:
            snapshot_failure_reason = "malformed_participations"

        run_id = self._start_run(
            organization_id=organization_id,
            connection_id=connection_id,
            region=region,
            environment=environment,
        )
        if run_id is None:
            return ReconciliationOutcome(
                succeeded=False,
                reason="database_failure",
                records_received=records_received,
                records_rejected=records_received,
            )

        if snapshot_failure_reason is not None:
            logger.info(
                "amazon marketplace reconciliation rejected reason=%s connection_id=%s",
                snapshot_failure_reason,
                connection_id,
            )
            self._complete_run(
                organization_id=organization_id,
                run_id=run_id,
                connection_id=connection_id,
                succeeded=False,
                records_received=records_received,
                records_accepted=0,
                records_rejected=records_received,
                failure_reason=snapshot_failure_reason,
            )
            return ReconciliationOutcome(
                succeeded=False,
                reason=snapshot_failure_reason,
                ingestion_run_id=run_id,
                records_received=records_received,
                records_rejected=records_received,
            )

        seller_account_id, failure_reason = self._reconcile_canonical_rows(
            organization_id=organization_id,
            connection_id=connection_id,
            region=region,
            selling_partner_id=spid,
            valid_participations=valid_participations,
        )

        self._complete_run(
            organization_id=organization_id,
            run_id=run_id,
            connection_id=connection_id,
            succeeded=failure_reason is None,
            records_received=records_received,
            records_accepted=len(valid_participations) if failure_reason is None else 0,
            records_rejected=0 if failure_reason is None else records_received,
            failure_reason=failure_reason,
        )

        if failure_reason is not None:
            return ReconciliationOutcome(
                succeeded=False,
                reason=failure_reason,
                ingestion_run_id=run_id,
                records_received=records_received,
                records_rejected=records_received,
            )

        return ReconciliationOutcome(
            succeeded=True,
            seller_account_id=seller_account_id,
            ingestion_run_id=run_id,
            records_received=records_received,
            records_accepted=len(valid_participations),
            records_rejected=0,
        )

    def _start_run(
        self,
        *,
        organization_id: UUID,
        connection_id: UUID,
        region: str,
        environment: str,
    ) -> UUID | None:
        try:
            with session_scope() as session:
                run = AmazonIngestionRunRepository(session).start(
                    organization_id=organization_id,
                    domain=INGESTION_DOMAIN,
                    region=region,
                    environment=environment,
                    connection_id=connection_id,
                )
                return run.id
        except (TypeError, SQLAlchemyError):
            # `start()` raises `TypeError` for an ambiguous/cross-organization
            # connection binding, not a `SQLAlchemyError` — this must degrade
            # to the same sanitized `database_failure` outcome as an actual
            # database error, never propagate as an unhandled exception out
            # of a `POST /connection/test` or OAuth-callback request. No run
            # row exists in either case, so there is nothing to mark failed.
            logger.warning(
                "amazon marketplace reconciliation failed reason=database_failure phase=start_run "
                "connection_id=%s",
                connection_id,
            )
            return None

    def _reconcile_canonical_rows(
        self,
        *,
        organization_id: UUID,
        connection_id: UUID,
        region: str,
        selling_partner_id: str,
        valid_participations: list[NormalizedMarketplaceParticipation],
    ) -> tuple[UUID | None, ReconciliationFailureReason | None]:
        try:
            with session_scope() as session:
                accounts = AmazonSellerAccountRepository(session)
                participations_repo = AmazonMarketplaceParticipationRepository(session)
                display_store_name = _deterministic_display_store_name(valid_participations)
                seller_account = accounts.create_or_reconcile(
                    organization_id=organization_id,
                    selling_partner_id=selling_partner_id,
                    display_store_name=display_store_name,
                )
                seen_marketplace_ids: set[str] = set()
                for participation in valid_participations:
                    participations_repo.create_or_reconcile(
                        organization_id=organization_id,
                        seller_account_id=seller_account.id,
                        marketplace_id=participation.marketplace_id,
                        region=region,
                        connection_id=connection_id,
                        name=participation.name,
                        country_code=participation.country_code,
                        default_currency_code=participation.default_currency_code,
                        default_language_code=participation.default_language_code,
                        domain_name=participation.domain_name,
                        store_name=participation.store_name,
                        is_participating=participation.is_participating,
                        has_suspended_listings=participation.has_suspended_listings,
                    )
                    seen_marketplace_ids.add(participation.marketplace_id)
                participations_repo.deactivate_missing(
                    organization_id=organization_id,
                    seller_account_id=seller_account.id,
                    seen_marketplace_ids=seen_marketplace_ids,
                )
                seller_account.last_successful_sync_at = datetime.now(UTC)
                return seller_account.id, None
        except SellerAccountOwnershipConflict:
            logger.info(
                "amazon marketplace reconciliation rejected reason=ownership_conflict connection_id=%s",
                connection_id,
            )
            return None, "ownership_conflict"
        except (TypeError, IntegrityError, SQLAlchemyError):
            logger.warning(
                "amazon marketplace reconciliation failed reason=database_failure phase=reconcile "
                "connection_id=%s",
                connection_id,
            )
            return None, "database_failure"

    def _complete_run(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        connection_id: UUID,
        succeeded: bool,
        records_received: int,
        records_accepted: int,
        records_rejected: int,
        failure_reason: ReconciliationFailureReason | None,
    ) -> None:
        try:
            with session_scope() as session:
                AmazonIngestionRunRepository(session).complete(
                    organization_id,
                    run_id,
                    status="succeeded" if succeeded else "failed",
                    records_received=records_received,
                    records_accepted=records_accepted,
                    records_rejected=records_rejected,
                    failure_class=failure_reason,
                )
        except SQLAlchemyError:
            logger.warning(
                "amazon marketplace reconciliation failed reason=database_failure phase=complete_run "
                "connection_id=%s run_id=%s",
                connection_id,
                run_id,
            )
