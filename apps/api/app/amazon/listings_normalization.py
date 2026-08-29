"""Deterministic, pure-function normalization from parsed Listings Items DTOs
to canonical `amazon_seller_listings` fields. 12B.3D.

No I/O, no database session, no Amazon call, no LLM. Every derived field
here is plain, deterministic Python logic over already-validated Pydantic
models from `app.amazon.listings_models` — this module never sees a raw
JSON payload.

Only `summaries`/`issues`/`offers`/`fulfillmentAvailability`/`productTypes`
are read (the approved 12B.3C/12B.3D `includedData` set). `attributes`,
`relationships`, and `procurement` are not modeled anywhere upstream (see
`listings_models.py`), so there is nothing here that could ingest them even
by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.amazon.listings_models import Item, ItemSummary

# Amazon's documented closed enum for ItemSummaryByMarketplace.status is
# exactly these two values (verified against the pinned contract report).
# Any *other* string is preserved verbatim in the stored `status` array
# (forward-compatible with a future third value) but simply never sets the
# corresponding derived boolean — an unknown value is neither "buyable" nor
# "discoverable" by definition, since neither derived flag is documented to
# mean anything else.
_BUYABLE_STATUS = "BUYABLE"
_DISCOVERABLE_STATUS = "DISCOVERABLE"

# Amazon's documented closed enum for Issue.severity, used only to rank
# "highest" — an unknown future severity string is preserved in the stored
# `issues` array but ranked below all three known values (never silently
# promoted to "highest" over a real ERROR/WARNING/INFO), and never crashes
# this ranking.
_SEVERITY_RANK = {"ERROR": 3, "WARNING": 2, "INFO": 1}


class ListingNormalizationError(Exception):
    """A single item cannot be deterministically normalized. Callers must
    reject the *whole* snapshot, not skip the item and continue — the same
    whole-snapshot-rejection principle already used for marketplace
    participations. Never carries the raw item, sku, or payload; only a
    short, sanitized reason string."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class NormalizedListing:
    """One canonical-row-shaped, already-validated listing. Field names and
    types mirror `AmazonSellerListing` exactly (see `models.py`)."""

    seller_sku: str
    asin: str | None
    product_type: str | None
    condition_type: str | None
    item_name: str | None
    main_image_url: str | None
    amazon_created_at: datetime | None
    amazon_last_updated_at: datetime | None
    status: list[str]
    is_buyable: bool
    is_discoverable: bool
    offers: list[dict]
    price_amount: Decimal | None
    price_currency: str | None
    fulfillment_availability: list[dict]
    issues: list[dict]
    issue_count: int
    highest_issue_severity: str | None
    product_types: list[dict]


def _select_marketplace_summary(item: Item, *, marketplace_id: str) -> ItemSummary | None:
    """Selects the one summary entry scoped to `marketplace_id`.

    Never infers marketplace identity from the response — `marketplace_id`
    always comes from the caller's own request scope (see
    `AmazonListingsIngestionService`), never from any entry inside `item`
    itself. Three outcomes:

    - Zero matching entries: not an error. `Item.summaries` is optional per
      the official schema, and Amazon returning entries for other
      marketplaces (or none at all) is not evidence of a malformed
      response — it just means no catalog data is available for the
      requested marketplace. Returns `None`; the caller normalizes with
      all summary-derived fields left at their nullable/false defaults.
    - Exactly one matching entry: the normal case.
    - More than one matching entry for the SAME marketplace id is a genuine
      ambiguity this function refuses to silently resolve by picking one —
      raises `ListingNormalizationError`.
    """
    matches = [s for s in (item.summaries or []) if s.marketplace_id == marketplace_id]
    if not matches:
        return None
    if len(matches) > 1:
        raise ListingNormalizationError("ambiguous_marketplace_summary")
    return matches[0]


def _select_price(item: Item, *, marketplace_id: str) -> tuple[Decimal | None, str | None]:
    """Consumer price, derived *only* from an eligible B2C offer scoped to
    the requested marketplace.

    12B.3D remediation: this previously fell back to ANY offer (including a
    B2B one) when no B2C offer existed for the marketplace — silently
    storing a business-to-business price in a field the approved schema
    documents as the *consumer* price. There is no eligible-offer fallback
    now: B2B-only, or no offers at all, both leave the derived price fields
    `None`. The complete `offers` array (every offer Amazon returned,
    B2C and B2B alike) is preserved separately and is unaffected by this —
    only this *derived, secondary* field is restricted.

    Multiple eligible B2C offers for the same marketplace (not expected,
    but not documented as impossible) are resolved deterministically by
    taking the first in Amazon's own response order, matching the
    established "not silently ambiguous, but also not a rejection" stance
    used elsewhere in this file for values that don't rise to the level of
    a genuine data-integrity problem.
    """
    eligible_b2c_offers = [
        o for o in (item.offers or []) if o.marketplace_id == marketplace_id and o.offer_type == "B2C"
    ]
    if not eligible_b2c_offers:
        return None, None
    chosen = eligible_b2c_offers[0]
    try:
        amount = Decimal(chosen.price.amount)
    except (InvalidOperation, TypeError):
        # The official schema types `amount` as its own `Decimal` string
        # format; a value that fails to parse as one is malformed, not a
        # zero/None price — reject the item rather than store a wrong price.
        raise ListingNormalizationError("malformed_offer_price") from None
    return amount, chosen.price.currency_code


def _highest_issue_severity(issues: list) -> str | None:
    if not issues:
        return None
    ranked = [(i, _SEVERITY_RANK.get(i.severity, 0)) for i in issues]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked[0][0].severity


def normalize_item(item: Item, *, marketplace_id: str) -> NormalizedListing:
    """Normalizes one already-parsed `Item` into a `NormalizedListing`.

    Raises `ListingNormalizationError` (sanitized reason only) for an
    ambiguous marketplace summary or a malformed offer price. Every other
    documented-optional field that is missing/empty is represented
    faithfully (`None` or `[]`), never guessed or defaulted to something
    Amazon did not say.
    """
    summary = _select_marketplace_summary(item, marketplace_id=marketplace_id)

    status: list[str] = list(summary.status) if summary is not None else []
    is_buyable = _BUYABLE_STATUS in status
    is_discoverable = _DISCOVERABLE_STATUS in status

    price_amount, price_currency = _select_price(item, marketplace_id=marketplace_id)

    issues = list(item.issues or [])
    highest_severity = _highest_issue_severity(issues)

    return NormalizedListing(
        seller_sku=item.sku,
        asin=summary.asin if summary is not None else None,
        product_type=summary.product_type if summary is not None else None,
        condition_type=summary.condition_type if summary is not None else None,
        item_name=summary.item_name if summary is not None else None,
        main_image_url=(summary.main_image.link if summary is not None and summary.main_image else None),
        amazon_created_at=summary.created_date if summary is not None else None,
        amazon_last_updated_at=summary.last_updated_date if summary is not None else None,
        status=status,
        is_buyable=is_buyable,
        is_discoverable=is_discoverable,
        offers=[o.model_dump(mode="json", by_alias=True) for o in (item.offers or [])],
        price_amount=price_amount,
        price_currency=price_currency,
        fulfillment_availability=[f.model_dump(mode="json", by_alias=True) for f in (item.fulfillment_availability or [])],
        issues=[i.model_dump(mode="json", by_alias=True) for i in issues],
        issue_count=len(issues),
        highest_issue_severity=highest_severity,
        product_types=[p.model_dump(mode="json", by_alias=True) for p in (item.product_types or [])],
    )
