"""Deterministic, pure-function normalization from parsed FBA Inventory DTOs
to canonical `amazon_seller_inventory`/`amazon_seller_inventory_observations`
fields. 12B.6B.

No I/O, no database session, no Amazon call. Every derived field here is
plain, deterministic Python logic over already-validated Pydantic models
from `app.amazon.inventory_models` — this module never sees a raw JSON
payload.

**Identity policy** (the required correction from the 12B.6B audit
approval): the pinned contract does not guarantee `sellerSku`, `fnSku`,
`asin`, or `condition` are present on every `InventorySummary` row (none
are in that object's `required` list — see `inventory_models.py`'s module
docstring). Rather than inventing a synthetic identifier for a row Amazon
did not identify, or silently collapsing distinct rows onto the same
identity, this module treats a row as identifiable **if and only if both
`sellerSku` and `condition` are present and non-blank** — `sellerSku` is
the seller's own catalog key (fundamentally how a seller's inventory is
addressed at all, even though this specific response schema does not
mark it required), and `condition` distinguishes multiple
condition-scoped listings of the same SKU, which the pinned schema's own
`InventorySummary.condition` field exists to represent. A row missing
either is rejected — never persisted, never assigned a fabricated key —
and counted via `records_rejected`, exactly matching how
`listings_normalization.py` rejects a whole item on a genuine data
anomaly rather than silently dropping or guessing.

`asin` and `fnSku` are preserved as source *attributes* only, never part
of the identity — both may legitimately be absent for a row this module
still accepts (e.g. an item not yet catalog-matched to an ASIN).

**Non-negativity policy** (also required by the audit approval): the
pinned schema places no `minimum` on any quantity field, so a database
CHECK constraint enforcing non-negativity would be stricter than Amazon's
own contract unless the application deliberately rejects a row that
violates it first. This module is that deliberate rejection point: any
quantity field present with a negative value fails the whole row (never
silently clamped to zero, never silently dropped from a subset of
fields), and the row is counted as rejected — the database CHECK is then
a legitimate defense-in-depth backstop that a correctly-functioning
ingestion should never actually trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.amazon.inventory_models import InventorySummary

# Amazon's own confirmed researchingQuantityBreakdown bucket names (pinned
# model's own enum) — see inventory_models.py.
_RESEARCHING_SHORT_TERM = "researchingQuantityInShortTerm"
_RESEARCHING_MID_TERM = "researchingQuantityInMidTerm"
_RESEARCHING_LONG_TERM = "researchingQuantityInLongTerm"


class InventoryNormalizationError(Exception):
    """A single summary cannot be deterministically normalized/identified.
    Callers reject only this one row (counted, not the whole page/run —
    see `inventory_ingestion.py`), never inventing a substitute identity.
    Never carries the raw summary, SKU, or payload; only a short,
    sanitized reason string."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class NormalizedInventoryObservation:
    """One canonical-row-shaped, already-validated inventory observation.
    Field names mirror `AmazonSellerInventory`/
    `AmazonSellerInventoryObservation` exactly (see `models.py`)."""

    seller_sku: str
    condition: str
    asin: str | None
    fnsku: str | None
    product_name: str | None
    total_quantity: int | None
    fulfillable_quantity: int | None
    inbound_working_quantity: int | None
    inbound_shipped_quantity: int | None
    inbound_receiving_quantity: int | None
    reserved_total_quantity: int | None
    reserved_pending_customer_order_quantity: int | None
    reserved_pending_transshipment_quantity: int | None
    reserved_fc_processing_quantity: int | None
    unfulfillable_total_quantity: int | None
    unfulfillable_customer_damaged_quantity: int | None
    unfulfillable_warehouse_damaged_quantity: int | None
    unfulfillable_distributor_damaged_quantity: int | None
    unfulfillable_carrier_damaged_quantity: int | None
    unfulfillable_defective_quantity: int | None
    unfulfillable_expired_quantity: int | None
    researching_total_quantity: int | None
    researching_quantity_short_term: int | None
    researching_quantity_mid_term: int | None
    researching_quantity_long_term: int | None
    amazon_last_updated_time: datetime | None


def _check_non_negative(value: int | None, *, field: str) -> None:
    if value is not None and value < 0:
        raise InventoryNormalizationError(f"negative_quantity_{field}")


def normalize_summary(summary: InventorySummary) -> NormalizedInventoryObservation:
    """Normalizes one already-parsed `InventorySummary` into a
    `NormalizedInventoryObservation`.

    Raises `InventoryNormalizationError` (sanitized reason only) when:
    - `sellerSku` or `condition` is missing/blank (`missing_identity`) —
      see module docstring's identity policy;
    - any quantity field is present but negative
      (`negative_quantity_<field>`) — see module docstring's
      non-negativity policy.

    Every other documented-optional field that is missing is represented
    faithfully as `None`, never guessed or defaulted to something Amazon
    did not say.
    """
    seller_sku = (summary.seller_sku or "").strip()
    condition = (summary.condition or "").strip()
    if not seller_sku or not condition:
        raise InventoryNormalizationError("missing_identity")

    details = summary.inventory_details
    fulfillable_quantity = details.fulfillable_quantity if details is not None else None
    inbound_working_quantity = details.inbound_working_quantity if details is not None else None
    inbound_shipped_quantity = details.inbound_shipped_quantity if details is not None else None
    inbound_receiving_quantity = details.inbound_receiving_quantity if details is not None else None

    reserved = details.reserved_quantity if details is not None else None
    reserved_total_quantity = reserved.total_reserved_quantity if reserved is not None else None
    reserved_pending_customer_order_quantity = (
        reserved.pending_customer_order_quantity if reserved is not None else None
    )
    reserved_pending_transshipment_quantity = (
        reserved.pending_transshipment_quantity if reserved is not None else None
    )
    reserved_fc_processing_quantity = reserved.fc_processing_quantity if reserved is not None else None

    unfulfillable = details.unfulfillable_quantity if details is not None else None
    unfulfillable_total_quantity = unfulfillable.total_unfulfillable_quantity if unfulfillable is not None else None
    unfulfillable_customer_damaged_quantity = (
        unfulfillable.customer_damaged_quantity if unfulfillable is not None else None
    )
    unfulfillable_warehouse_damaged_quantity = (
        unfulfillable.warehouse_damaged_quantity if unfulfillable is not None else None
    )
    unfulfillable_distributor_damaged_quantity = (
        unfulfillable.distributor_damaged_quantity if unfulfillable is not None else None
    )
    unfulfillable_carrier_damaged_quantity = (
        unfulfillable.carrier_damaged_quantity if unfulfillable is not None else None
    )
    unfulfillable_defective_quantity = unfulfillable.defective_quantity if unfulfillable is not None else None
    unfulfillable_expired_quantity = unfulfillable.expired_quantity if unfulfillable is not None else None

    researching = details.researching_quantity if details is not None else None
    researching_total_quantity = researching.total_researching_quantity if researching is not None else None
    researching_quantity_short_term: int | None = None
    researching_quantity_mid_term: int | None = None
    researching_quantity_long_term: int | None = None
    if researching is not None and researching.researching_quantity_breakdown:
        for entry in researching.researching_quantity_breakdown:
            if entry.name == _RESEARCHING_SHORT_TERM:
                researching_quantity_short_term = entry.quantity
            elif entry.name == _RESEARCHING_MID_TERM:
                researching_quantity_mid_term = entry.quantity
            elif entry.name == _RESEARCHING_LONG_TERM:
                researching_quantity_long_term = entry.quantity
            # An unrecognized future bucket name is preserved nowhere
            # (there is no catch-all column) but never crashes
            # normalization — forward-compatible with a bucket Amazon
            # adds later, at the cost of that bucket's quantity not being
            # separately visible until this module is updated to know it.

    quantities: dict[str, int | None] = {
        "total_quantity": summary.total_quantity,
        "fulfillable_quantity": fulfillable_quantity,
        "inbound_working_quantity": inbound_working_quantity,
        "inbound_shipped_quantity": inbound_shipped_quantity,
        "inbound_receiving_quantity": inbound_receiving_quantity,
        "reserved_total_quantity": reserved_total_quantity,
        "reserved_pending_customer_order_quantity": reserved_pending_customer_order_quantity,
        "reserved_pending_transshipment_quantity": reserved_pending_transshipment_quantity,
        "reserved_fc_processing_quantity": reserved_fc_processing_quantity,
        "unfulfillable_total_quantity": unfulfillable_total_quantity,
        "unfulfillable_customer_damaged_quantity": unfulfillable_customer_damaged_quantity,
        "unfulfillable_warehouse_damaged_quantity": unfulfillable_warehouse_damaged_quantity,
        "unfulfillable_distributor_damaged_quantity": unfulfillable_distributor_damaged_quantity,
        "unfulfillable_carrier_damaged_quantity": unfulfillable_carrier_damaged_quantity,
        "unfulfillable_defective_quantity": unfulfillable_defective_quantity,
        "unfulfillable_expired_quantity": unfulfillable_expired_quantity,
        "researching_total_quantity": researching_total_quantity,
        "researching_quantity_short_term": researching_quantity_short_term,
        "researching_quantity_mid_term": researching_quantity_mid_term,
        "researching_quantity_long_term": researching_quantity_long_term,
    }
    for field, value in quantities.items():
        _check_non_negative(value, field=field)

    return NormalizedInventoryObservation(
        seller_sku=seller_sku,
        condition=condition,
        asin=(summary.asin or None),
        fnsku=(summary.fn_sku or None),
        product_name=(summary.product_name or None),
        amazon_last_updated_time=summary.last_updated_time,
        **quantities,
    )
