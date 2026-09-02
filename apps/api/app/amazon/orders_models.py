"""External-provider DTOs for SP-API Orders v2026-01-01. Not the ASI canonical data model.

Pinned against `docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md` and
re-verified directly against the primary-source model file during 12B.4C
(SHA-256 `4bd47126b466b94ccb04d822e3b94edee1fa7977a8b376a8c329852ef61be431`,
identical to the hash 12B.4A recorded — no drift between the two passes).

## Non-PII scope (12B.4C)

12B.4 never requests `includedData=BUYER/RECIPIENT/PAYMENT/TAX` (see
`orders_client.APPROVED_INCLUDED_DATA`), so `Order.buyer`, `.recipient`,
`.payment`, `.tax` should never arrive in a real response. Defense in
depth (fixture `16_restricted_pii_fields_present.json` proves this):
those four fields, plus every other confirmed PII/PII-adjacent field, are
**not declared anywhere in this module** — not typed, not aliased, not
even as an ignored placeholder. Every model below uses
`model_config = ConfigDict(extra="ignore")`, so any undeclared key in a
real Amazon response (a PII field returned unexpectedly, or a brand-new
field from a future contract revision) is silently dropped during
`model_validate()` and never becomes a Python attribute — it cannot reach
a log line, an exception, or a future persistence layer through this
model, because it never enters the model's `__dict__` at all. This is a
stronger guarantee than "redact before logging": the excluded data is
never parsed into any addressable value in the first place.

Deliberately never declared, anywhere in this module, matching the 12B.4A
privacy boundary exactly:

- `Order.buyer`, `Order.recipient`, `Order.payment`, `Order.tax`,
  `Order.fulfillmentOrders` (EasyShip-only, low value, out of scope).
- `OrderItem.expense`, `OrderItem.promotion` (excluded `includedData`
  categories, out of scope for the first slice).
- `ItemProduct.serialNumbers`, `ItemProduct.customization` — both
  unconditional (always present on `product`, gated by no `includedData`
  flag at all) and both out of scope: `customization.customizedUrl` can
  point at customer-submitted personalization content.
- `ItemCancellationRequest.cancelReason`,
  `ItemCancellationExecution.cancelReason` — only the enum
  `requester`/`cancelledBy` siblings are modeled; the free-text reason
  fields sharing the same `CANCELLATION`-gated object are not.
- `GiftOption.giftMessage` — only `giftWrapLevel` (a non-PII enum-ish
  string) is modeled from the same `FULFILLMENT`-gated
  `packing.giftOption` object; the free-text gift message sharing that
  object is not.
- `OrderPackage.shipFromAddress` (the seller's own warehouse address —
  not customer PII, but out of scope to keep "no address data at all"
  simple and audit-friendly) and `.packageItems` (not needed).
- `ItemFulfillment.picking`, `.shipping` — out of scope for this narrow
  first slice (see this module's own note on `ItemFulfillment` below).

## Forward-compatibility boundary (12B.4C, Phase 3)

Two different kinds of model live in this file, and they intentionally use
opposite `extra=` policies:

- **Amazon-shaped response models** (`Order`, `OrderItem`, `SalesChannel`,
  `Money`, every nested type mirroring a field in the official schema) use
  `extra="ignore"`. Amazon owns this schema and can add fields at any time
  (proven by fixture `15_unknown_additive_fields.json`); a client that
  fails closed on an unrecognized field would break in production the
  first time Amazon shipped a routine additive change. Forward
  compatibility here is a correctness requirement, not a convenience.
- **ASI-owned result/provenance wrappers** (`OrdersPage`, `OrderResult`,
  `OrdersPageProvenance`) use `extra="forbid"`. Nothing external ever
  calls `model_validate()` on these with untrusted input — they are always
  constructed by this codebase's own client code from already-known
  fields. `extra="forbid"` here catches an internal programming mistake
  (a stray/renamed keyword argument) at construction time; it has no
  bearing on Amazon contract drift at all, since Amazon never populates
  these types directly.

## Null vs. missing (unchanged from Listings' 12B.3C finding)

The pinned model is Swagger 2.0 and contains zero `nullable`/`x-nullable`
occurrences (re-verified directly against the primary source during
12B.4C, identical method to 12B.3C's Listings finding). Every optional
field in this file is therefore typed with `optional_not_null(X)`
(imported from `listings_models`, not duplicated — a small, provider-
agnostic Pydantic helper), never plain `X | None`: a missing key produces
`None` (no validator runs), but an explicit JSON `null` correctly *fails*
validation, since the schema never documents that any field may hold one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.amazon.listings_models import optional_not_null


def _reject_float(value: object) -> object:
    """`Money.amount` is documented as `Decimal` — "A decimal number with no
    loss of precision" — and is transmitted as a JSON *string*
    (`"type": "string"` in the pinned model), never a JSON number. A raw
    Python `float` must never reach this field: floats cannot represent
    every decimal value exactly, which is precisely the precision loss the
    contract's own `Decimal` type description rules out. Pydantic would
    otherwise happily coerce a JSON number into `Decimal` (losing no
    precision on the JSON-parse side, since `orjson`/`json` already parsed
    it as a Python `float` first) — this validator makes that path fail
    loudly instead of silently accepting a value that already lost
    precision one step earlier, in the JSON parser."""
    if isinstance(value, float):
        raise ValueError("monetary amount must be a decimal string, not a float")
    return value


DecimalAmount = Annotated[Decimal, BeforeValidator(_reject_float)]


class Money(BaseModel):
    model_config = ConfigDict(extra="ignore")

    amount: DecimalAmount
    currency_code: str = Field(alias="currencyCode")


class Alias(BaseModel):
    model_config = ConfigDict(extra="ignore")

    alias_id: str = Field(alias="aliasId")
    alias_type: str = Field(alias="aliasType")


class AssociatedOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")

    order_id: optional_not_null(str) = Field(default=None, alias="orderId")
    association_type: optional_not_null(str) = Field(default=None, alias="associationType")


class SalesChannel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    channel_name: str = Field(alias="channelName")
    marketplace_id: optional_not_null(str) = Field(default=None, alias="marketplaceId")
    marketplace_name: optional_not_null(str) = Field(default=None, alias="marketplaceName")


class OrderProceedsBreakdown(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    status: optional_not_null(str) = None
    subtotal: Money


class OrderProceeds(BaseModel):
    model_config = ConfigDict(extra="ignore")

    grand_total: optional_not_null(Money) = Field(default=None, alias="grandTotal")
    breakdowns: optional_not_null(list[OrderProceedsBreakdown]) = None


class OrderFulfillment(BaseModel):
    """`fulfillmentStatus` is the only field the official schema requires on
    this object. Only `fulfilledBy`/`fulfillmentServiceLevel` are modeled
    beyond it — nothing else on `OrderFulfillment` is in the allowed slice."""

    model_config = ConfigDict(extra="ignore")

    fulfillment_status: str = Field(alias="fulfillmentStatus")
    fulfilled_by: optional_not_null(str) = Field(default=None, alias="fulfilledBy")
    fulfillment_service_level: optional_not_null(str) = Field(default=None, alias="fulfillmentServiceLevel")


class PackageStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    detailed_status: optional_not_null(str) = Field(default=None, alias="detailedStatus")


class OrderPackage(BaseModel):
    """`shipFromAddress` (`MerchantAddress` — the seller's own warehouse
    address) and `packageItems` (`PackageItem[]`) exist on the official
    schema but are deliberately not modeled. Both were explicitly
    field-level audited during 12B.4C (see
    `docs/AI_HANDOVER/12B4C_ORDERS_CLIENT.md`'s deferred-fulfillment-fields
    audit, not just noted as generically out of scope):

    - `shipFromAddress`: **intentionally deferred**, not prohibited — it
      is not customer PII (it is the seller's own facility address), but
      12B.4A's original reasoning (kept, not revisited) is to preserve a
      simple, audit-friendly "no address data anywhere in this schema"
      invariant rather than partially excepting one non-customer address.
    - `packageItems[].orderItemId`/`.quantity`: **intentionally deferred**
      (not prohibited) — non-PII order/item linkage that would be safe to
      add for reconciling which items shipped in which package, but is
      not needed by this slice's stated analytics goals.
    - `packageItems[].transparencyCodes`: **prohibited** — Amazon
      Transparency serialization codes tied to a specific physical unit,
      the same supply-chain-sensitivity class this module already
      excludes `ItemProduct.serialNumbers` for. If `packageItems` is ever
      added, this field must still never be modeled.
    """

    model_config = ConfigDict(extra="ignore")

    package_reference_id: str = Field(alias="packageReferenceId")
    created_time: optional_not_null(datetime) = Field(default=None, alias="createdTime")
    package_status: optional_not_null(PackageStatus) = Field(default=None, alias="packageStatus")
    carrier: optional_not_null(str) = None
    ship_time: optional_not_null(datetime) = Field(default=None, alias="shipTime")
    shipping_service: optional_not_null(str) = Field(default=None, alias="shippingService")
    tracking_number: optional_not_null(str) = Field(default=None, alias="trackingNumber")


class ItemCondition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    condition_type: optional_not_null(str) = Field(default=None, alias="conditionType")
    condition_subtype: optional_not_null(str) = Field(default=None, alias="conditionSubtype")
    condition_note: optional_not_null(str) = Field(default=None, alias="conditionNote")


class ItemPrice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    unit_price: optional_not_null(Money) = Field(default=None, alias="unitPrice")
    price_designation: optional_not_null(str) = Field(default=None, alias="priceDesignation")


class ItemProduct(BaseModel):
    """`serialNumbers` and `customization` exist on the official schema but
    are deliberately not modeled here — see module docstring. Both are
    unconditional (always present when the item exists, gated by no
    `includedData` flag), which is exactly why omitting them from this
    model, rather than trying to filter them at request time, is the only
    way to exclude them."""

    model_config = ConfigDict(extra="ignore")

    asin: optional_not_null(str) = None
    title: optional_not_null(str) = None
    seller_sku: optional_not_null(str) = Field(default=None, alias="sellerSku")
    condition: optional_not_null(ItemCondition) = None
    price: optional_not_null(ItemPrice) = None


class ItemProceedsBreakdown(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    subtotal: Money


class ItemProceeds(BaseModel):
    model_config = ConfigDict(extra="ignore")

    proceeds_total: optional_not_null(Money) = Field(default=None, alias="proceedsTotal")
    breakdowns: optional_not_null(list[ItemProceedsBreakdown]) = None


class ItemCancellationRequest(BaseModel):
    """`cancelReason` (free-text, buyer-authored) exists on the official
    schema alongside `requester` but is deliberately not modeled — see
    module docstring and fixture `06_cancelled_order.json`."""

    model_config = ConfigDict(extra="ignore")

    requester: optional_not_null(str) = None


class ItemCancellationExecution(BaseModel):
    """`cancelReason` (free-text) exists on the official schema alongside
    `cancelledBy` but is deliberately not modeled — see module docstring."""

    model_config = ConfigDict(extra="ignore")

    cancelled_by: optional_not_null(str) = Field(default=None, alias="cancelledBy")


class ItemCancellation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cancellation_request: optional_not_null(ItemCancellationRequest) = Field(
        default=None, alias="cancellationRequest"
    )
    cancellation_execution: optional_not_null(ItemCancellationExecution) = Field(
        default=None, alias="cancellationExecution"
    )


class GiftOption(BaseModel):
    """`giftMessage` (free-text, buyer-authored) exists on the official
    schema alongside `giftWrapLevel` but is deliberately not modeled — see
    module docstring and fixture `16_restricted_pii_fields_present.json`.
    `giftWrapLevel` is a service-tier label ("PREMIUM", etc.), not
    customer-authored content."""

    model_config = ConfigDict(extra="ignore")

    gift_wrap_level: optional_not_null(str) = Field(default=None, alias="giftWrapLevel")


class ItemPacking(BaseModel):
    """`serialNumberRequirement` exists on the official schema but is out of
    scope for this slice (no stated analytics use) and not modeled."""

    model_config = ConfigDict(extra="ignore")

    gift_option: optional_not_null(GiftOption) = Field(default=None, alias="giftOption")


class ItemFulfillment(BaseModel):
    """`picking` and `shipping` exist on the official schema and are
    deliberately not modeled. Both were explicitly field-level audited
    during 12B.4C (see `docs/AI_HANDOVER/12B4C_ORDERS_CLIENT.md`'s
    deferred-fulfillment-fields audit) rather than left as a generic "out
    of scope" note:

    - `picking.substitutionPreference.substitutionType`/
      `.substitutionOptions[]` (`ItemSubstitutionOption`: asin,
      sellerSku, title, quantityOrdered, measurement): **safe and
      valuable, deliberately not added in 12B.4C** — every underlying
      field mirrors an already-approved non-PII `ItemProduct`-shaped
      field, but this milestone's brief is client/parser only; adding it
      is a conscious choice for a future increment, not an oversight.
    - `shipping.scheduledDeliveryWindow` (`DateTimeRange`): **intentionally
      deferred** — plain timestamps, non-PII, but no stated analytics use
      in this slice.
    - `shipping.shippingConstraints` (`ItemShippingConstraints`: pallet
      delivery / cash-on-delivery / signature / recipient-identity /
      recipient-age verification flags): **intentionally deferred** —
      non-PII operational/compliance flags, marginal analytics value
      today.
    - `shipping.internationalShipping.iossNumber`: **prohibited** — an EU
      VAT (Import One-Stop Shop) registration number. It is the seller's
      own registration, not customer PII, but it is the same sensitivity
      class as the already-excluded `TAX`-gated `taxRegistrationNumber`
      (a tax-registration identifier) and must never be modeled if
      `internationalShipping` is otherwise ever added.
    """

    model_config = ConfigDict(extra="ignore")

    quantity_fulfilled: optional_not_null(int) = Field(default=None, alias="quantityFulfilled")
    quantity_unfulfilled: optional_not_null(int) = Field(default=None, alias="quantityUnfulfilled")
    packing: optional_not_null(ItemPacking) = None


class OrderItem(BaseModel):
    """`measurement`, `associatedOrderItems`, `programs` (item-level),
    `expense`, `promotion`, and item-level `tax` exist on the official
    schema but are out of scope for this slice and not modeled — none
    appear in 12B.4A's allowed-slice table."""

    model_config = ConfigDict(extra="ignore")

    order_item_id: str = Field(alias="orderItemId")
    quantity_ordered: int = Field(alias="quantityOrdered")
    product: ItemProduct
    proceeds: optional_not_null(ItemProceeds) = None
    cancellation: optional_not_null(ItemCancellation) = None
    fulfillment: optional_not_null(ItemFulfillment) = None


class Order(BaseModel):
    """`buyer`, `recipient`, `payment`, `tax`, `fulfillmentOrders` exist on
    the official schema but are never modeled here — see module docstring.
    `orderId`, `createdTime`, `lastUpdatedTime`, `salesChannel`,
    `orderItems` are the official schema's required fields; everything
    else here is independently optional."""

    model_config = ConfigDict(extra="ignore")

    order_id: str = Field(alias="orderId")
    created_time: datetime = Field(alias="createdTime")
    last_updated_time: datetime = Field(alias="lastUpdatedTime")
    sales_channel: SalesChannel = Field(alias="salesChannel")
    order_items: list[OrderItem] = Field(alias="orderItems")
    order_aliases: optional_not_null(list[Alias]) = Field(default=None, alias="orderAliases")
    programs: optional_not_null(list[str]) = None
    associated_orders: optional_not_null(list[AssociatedOrder]) = Field(default=None, alias="associatedOrders")
    proceeds: optional_not_null(OrderProceeds) = None
    fulfillment: optional_not_null(OrderFulfillment) = None
    packages: optional_not_null(list[OrderPackage]) = None


class Pagination(BaseModel):
    """The documented end-of-pagination signal is the *absence* of the
    `nextToken` key from the response, not an empty string and not an
    explicit `null` (same convention as Listings' `ListingsPagination`)."""

    model_config = ConfigDict(extra="ignore")

    next_token: optional_not_null(str) = Field(default=None, alias="nextToken")


class SearchOrdersResponse(BaseModel):
    """Matches the official `SearchOrdersResponse` schema. `orders` is
    required (an empty list is a valid, structurally-complete zero-result
    page — fixture `08_empty_result.json`); `pagination` is optional
    (absent-only). `lastUpdatedBefore`/`createdBefore` also exist as
    optional top-level echo fields on the official schema but are not
    modeled: this client already knows what it requested (see
    `OrdersPage`, which echoes the caller's own request, never anything
    read from the response body)."""

    model_config = ConfigDict(extra="ignore")

    orders: list[Order]
    pagination: optional_not_null(Pagination) = None


class GetOrderResponse(BaseModel):
    """Matches the official `GetOrderResponse` schema exactly: a single
    required `order` field, no envelope wrapper beyond that."""

    model_config = ConfigDict(extra="ignore")

    order: Order


class OrdersPageProvenance(BaseModel):
    """Non-secret connectivity metadata. Never include tokens, authorization
    headers, or raw response bodies. Mirrors `ListingsPageProvenance`
    exactly; see that model's docstring for why `rate_limit`/`request_id`
    are sanitized by the client before reaching this model."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "amazon_sp_api"
    api: str = "orders"
    operation: str
    region: str
    endpoint_host: str
    fetched_at: datetime
    http_status: int
    api_model_version: str
    attempt_count: int
    rate_limit: str | None = None
    request_id: str | None = None


class OrdersPage(BaseModel):
    """One fetched-and-parsed official `searchOrders` page. `marketplace_ids`
    always echoes the caller's own *request*, never anything read from the
    response body. `next_token` is returned so a future durable caller
    (12B.4D) can decide whether and how to fetch another page — this
    client never loops on it itself."""

    model_config = ConfigDict(extra="forbid")

    orders: list[Order]
    next_token: str | None
    marketplace_ids: tuple[str, ...]
    pagination_token_used: str | None
    provenance: OrdersPageProvenance


class OrderResult(BaseModel):
    """One fetched-and-parsed official `getOrder` result."""

    model_config = ConfigDict(extra="forbid")

    order: Order
    provenance: OrdersPageProvenance
