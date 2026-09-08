"""External-provider DTOs for FBA Inventory API v1 `getInventorySummaries`.
Not the ASI canonical data model.

Pinned against the official model file at
`amzn/selling-partner-api-models@cca8d338b6dc56afd3f9fdb822d2125a80ae8090`,
`models/fba-inventory-api-model/fbaInventory.json`
(SHA-256 `7c14bcdb22de8ca2df45e5a40f2a422cff344d45985a68b9515b2e800edcc5ab`
of that exact fetch — see
`docs/AI_HANDOVER/12B6B_FBA_INVENTORY_INGESTION.md`).

Design note on required vs optional (verified directly against the pinned
model — a Swagger 2.0 document): `InventorySummary` itself has **no**
`required` list at all — every one of `asin`, `fnSku`, `sellerSku`,
`condition`, `productName`, `totalQuantity`, `lastUpdatedTime`, `stores`,
`inventoryDetails` is independently optional. The same is true of
`InventoryDetails`, `ReservedQuantity`, and `UnfulfillableQuantity` — none
of their properties are required. The one nested object that *does*
declare `required` is `ResearchingQuantityEntry` (`name`, `quantity`, both
required when an entry is present at all), and the top-level
`GetInventorySummariesResult` (`granularity`, `inventorySummaries`, both
required — though `inventorySummaries` may be an empty list).

Swagger 2.0 has no `nullable`/`x-nullable` keyword anywhere in this file
(verified directly, same as `listings_models.py`'s own finding for the
Listings Items contract) — so, as there, "optional" means "the key may be
absent," never "the key may be present with an explicit JSON `null`."
Every optional field below uses the same `optional_not_null` helper
`listings_models.py` already established, reused here rather than
duplicated.

Design note on numeric constraints: no field in this pinned schema
documents a `minimum`/`maximum` on any quantity — every quantity is plain
`"type": "integer"`. No `ge=0` is added here for that reason (inventing a
stricter bound than Amazon's own contract would risk rejecting a value
Amazon is fully entitled to send); non-negativity is instead an
*application-layer* policy enforced during normalization (see
`inventory_normalization.py`), which explicitly rejects and counts a row
with a negative quantity rather than silently accepting or crashing on it.

`condition` carries no enum constraint in the pinned contract (plain
`"type": "string"`) — modeled as `str`, never a closed `Literal`/enum, for
the same reason `listings_models.py` avoids `Literal` for Amazon-owned
enums it does not control.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.amazon.listings_models import optional_not_null

__all__ = [
    "optional_not_null",
    "ReservedQuantity",
    "UnfulfillableQuantity",
    "ResearchingQuantityEntry",
    "ResearchingQuantity",
    "InventoryDetails",
    "InventorySummary",
    "Granularity",
    "InventoryPagination",
    "SpApiErrorEntry",
    "GetInventorySummariesResult",
    "InventoryPage",
    "InventoryPageProvenance",
]

# Amazon's own confirmed enum for ResearchingQuantityEntry.name (pinned
# model, x-docgen-enum-table-extension) — Short Term 1-10 days, Mid Term
# 11-20 days, Long Term 21+ days.
RESEARCHING_QUANTITY_SHORT_TERM = "researchingQuantityInShortTerm"
RESEARCHING_QUANTITY_MID_TERM = "researchingQuantityInMidTerm"
RESEARCHING_QUANTITY_LONG_TERM = "researchingQuantityInLongTerm"


class ReservedQuantity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_reserved_quantity: optional_not_null(int) = Field(default=None, alias="totalReservedQuantity")
    pending_customer_order_quantity: optional_not_null(int) = Field(
        default=None, alias="pendingCustomerOrderQuantity"
    )
    pending_transshipment_quantity: optional_not_null(int) = Field(
        default=None, alias="pendingTransshipmentQuantity"
    )
    fc_processing_quantity: optional_not_null(int) = Field(default=None, alias="fcProcessingQuantity")


class UnfulfillableQuantity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_unfulfillable_quantity: optional_not_null(int) = Field(default=None, alias="totalUnfulfillableQuantity")
    customer_damaged_quantity: optional_not_null(int) = Field(default=None, alias="customerDamagedQuantity")
    warehouse_damaged_quantity: optional_not_null(int) = Field(default=None, alias="warehouseDamagedQuantity")
    distributor_damaged_quantity: optional_not_null(int) = Field(default=None, alias="distributorDamagedQuantity")
    carrier_damaged_quantity: optional_not_null(int) = Field(default=None, alias="carrierDamagedQuantity")
    defective_quantity: optional_not_null(int) = Field(default=None, alias="defectiveQuantity")
    expired_quantity: optional_not_null(int) = Field(default=None, alias="expiredQuantity")


class ResearchingQuantityEntry(BaseModel):
    """Both fields are genuinely required by the pinned schema whenever an
    entry is present at all — unlike every other model in this file."""

    model_config = ConfigDict(extra="ignore")

    name: str
    quantity: int


class ResearchingQuantity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_researching_quantity: optional_not_null(int) = Field(default=None, alias="totalResearchingQuantity")
    researching_quantity_breakdown: optional_not_null(list[ResearchingQuantityEntry]) = Field(
        default=None, alias="researchingQuantityBreakdown"
    )


class InventoryDetails(BaseModel):
    """Absent entirely (not merely empty) unless the request's `details`
    query parameter is `true` — matches the pinned schema's own
    description verbatim."""

    model_config = ConfigDict(extra="ignore")

    fulfillable_quantity: optional_not_null(int) = Field(default=None, alias="fulfillableQuantity")
    inbound_working_quantity: optional_not_null(int) = Field(default=None, alias="inboundWorkingQuantity")
    inbound_shipped_quantity: optional_not_null(int) = Field(default=None, alias="inboundShippedQuantity")
    inbound_receiving_quantity: optional_not_null(int) = Field(default=None, alias="inboundReceivingQuantity")
    reserved_quantity: optional_not_null(ReservedQuantity) = Field(default=None, alias="reservedQuantity")
    researching_quantity: optional_not_null(ResearchingQuantity) = Field(default=None, alias="researchingQuantity")
    unfulfillable_quantity: optional_not_null(UnfulfillableQuantity) = Field(
        default=None, alias="unfulfillableQuantity"
    )


class InventorySummary(BaseModel):
    """One inventory summary entry. Every field is independently optional
    per the pinned schema — see module docstring. No field here is ever
    assumed present; identity/quantity handling is entirely the job of
    `inventory_normalization.py`, not this parsing layer."""

    model_config = ConfigDict(extra="ignore")

    asin: optional_not_null(str) = None
    fn_sku: optional_not_null(str) = Field(default=None, alias="fnSku")
    seller_sku: optional_not_null(str) = Field(default=None, alias="sellerSku")
    condition: optional_not_null(str) = None
    inventory_details: optional_not_null(InventoryDetails) = Field(default=None, alias="inventoryDetails")
    last_updated_time: optional_not_null(datetime) = Field(default=None, alias="lastUpdatedTime")
    product_name: optional_not_null(str) = Field(default=None, alias="productName")
    total_quantity: optional_not_null(int) = Field(default=None, alias="totalQuantity")
    stores: optional_not_null(list[str]) = None


class Granularity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    granularity_type: optional_not_null(str) = Field(default=None, alias="granularityType")
    granularity_id: optional_not_null(str) = Field(default=None, alias="granularityId")


class InventoryPagination(BaseModel):
    """`nextToken`'s only documented lifetime is 30 seconds from creation
    — see `inventory_client.py`/`inventory_ingestion.py` for how that
    constrains this ingestion's design (no durable cross-attempt
    persistence of this value)."""

    model_config = ConfigDict(extra="ignore")

    next_token: optional_not_null(str) = Field(default=None, alias="nextToken")


class SpApiErrorEntry(BaseModel):
    """One entry of the pinned schema's `Error` object — `code` is the
    only required field; `message`/`details` are independently optional.
    Structural only: this model exists so `inventory_client.py` can tell
    whether Amazon explained an absent `payload` with its own error
    object, never to surface `message`/`details` text anywhere (both may
    describe the request in terms that echo seller-identifying
    parameters) — callers of this model must only ever read `code`."""

    model_config = ConfigDict(extra="ignore")

    code: str
    message: optional_not_null(str) = None
    details: optional_not_null(str) = None


class GetInventorySummariesResult(BaseModel):
    """Both fields are genuinely required by the pinned schema —
    `inventorySummaries` may still be an empty list (a seller with no
    inventory at this marketplace is a valid, complete response, not a
    malformed one)."""

    model_config = ConfigDict(extra="ignore")

    granularity: Granularity
    inventory_summaries: list[InventorySummary] = Field(alias="inventorySummaries")


class GetInventorySummariesResponse(BaseModel):
    """Top-level response envelope. `payload` is documented optional (an
    error-only response omits it); `errors` likewise — both were
    previously unparsed here (`errors` silently dropped by `extra=
    "ignore"`), which meant a `payload`-absent response could never be
    distinguished from an Amazon-explained error from a genuinely
    undocumented empty state. `errors` is now parsed structurally (see
    `SpApiErrorEntry`) so `inventory_client.py` can tell them apart. This
    client still treats a response with no `payload` and no `errors` as
    a parse failure, never as an empty result — the pinned contract's
    own documented shape for a seller with zero FBA inventory is
    `payload` *present* with `inventorySummaries: []`, not `payload`
    absent (see `GetInventorySummariesResult`'s own docstring)."""

    model_config = ConfigDict(extra="ignore")

    payload: optional_not_null(GetInventorySummariesResult) = None
    pagination: optional_not_null(InventoryPagination) = None
    errors: optional_not_null(list[SpApiErrorEntry]) = None


class InventoryPageProvenance(BaseModel):
    """Non-secret connectivity metadata. Never includes tokens,
    authorization headers, or raw response bodies. Mirrors
    `ListingsPageProvenance` exactly."""

    model_config = ConfigDict(extra="ignore")

    provider: str = "amazon_sp_api"
    api: str = "fba-inventory"
    operation: str
    region: str
    endpoint_host: str
    fetched_at: datetime
    http_status: int
    api_model_version: str
    attempt_count: int
    rate_limit: str | None = None
    request_id: str | None = None


class InventoryPage(BaseModel):
    """One fetched-and-parsed official API page. `marketplace_id` always
    echoes the caller's own request, never anything read from the response
    body."""

    model_config = ConfigDict(extra="ignore")

    granularity: Granularity
    summaries: list[InventorySummary]
    next_token: str | None
    marketplace_id: str
    page_token_used: str | None
    provenance: InventoryPageProvenance
