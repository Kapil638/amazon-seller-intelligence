"""External-provider DTOs for SP-API Listings Items v2021-08-01. Not the ASI canonical data model.

Pinned against the official contract report at
`docs/AI_HANDOVER/12B3A_LISTINGS_ITEMS_API_CONTRACT_REPORT.md`. 12B.3C scope
only: `summaries`, `issues`, `offers`, `fulfillmentAvailability`,
`productTypes`. `attributes`, `relationships`, and `procurement` are
deliberately not modeled here at all — with every model below using
`extra="ignore"`, even a response that included them would silently drop
them, never store them, and never let them influence validation.

Design note on enum-like string fields (`status`, `severity`, `offerType`,
`fulfillmentChannelCode`, `conditionType`): these are modeled as plain
`str`/`list[str]`, never `Literal[...]`, even though the official schema
currently documents closed value sets for some of them. A `Literal` would
make the *entire page* fail to parse the moment Amazon adds a value we
haven't seen — for an external vendor's evolving enum, that is exactly the
kind of fragility this client must avoid. Strictness here comes from
required-vs-optional field *presence* (enforced below), not from closed
value sets of fields we do not control.

Design note on numeric constraints: the official schema documents `minimum:
0` on exactly one field in this slice — `FulfillmentAvailability.quantity`
— which is enforced below (`ge=0`). `numberOfResults`, `ItemImage.height`/
`width`, and `Points.pointsNumber` are plain `"type": "integer"` with no
documented minimum in the pinned spec, so none is added here: inventing a
stricter constraint than Amazon's own contract would risk rejecting a
value Amazon is fully entitled to send.

Design note on required vs optional fields, and on null vs absent
(corrected 12B.3C review): every field below is typed to match the
official schema's own `required` list exactly, but "optional" and
"nullable" are NOT the same claim, and an earlier version of this file
conflated them. The pinned spec is a Swagger 2.0 document; verified
directly against it, `nullable`/`x-nullable` appears **zero times** anywhere
in the file. Swagger 2.0 has no mechanism at all for declaring a field
nullable — every property's `type` is a single concrete type. This means:
for every field in this contract, "optional" can only ever mean "the key
may be absent," never "the key may be present with an explicit JSON
`null`" — there is no schema text anywhere that permits the latter for any
field, required or optional.

- A field in Amazon's `required` list (e.g. `Item.sku`, `ItemSearchResults.
  items`, `ItemSearchResults.numberOfResults`) is typed with no default and
  a non-Optional type. A missing key OR an explicit JSON `null` both fail
  Pydantic validation the same way — correctly, since the official schema
  permits neither. This is what makes `items: []` (a genuinely valid,
  empty, zero-result page) distinguishable from `items` missing or `items:
  null` (a malformed top-level payload): only the first passes validation.
- A field Amazon documents as optional (e.g. `Item.summaries`, `ItemSummary.
  asin`, `ItemSearchResults.pagination`, `ListingsPagination.nextToken`) is
  typed with the `optional_not_null(X)` helper below, not plain `X | None`.
  A missing key still produces `None` (Pydantic does not validate a field's
  default when the key is absent, so no validator runs at all in that
  case). An explicit JSON `null`, however, now correctly **fails**
  validation — the official schema never documents that any field may hold
  `null`, so this client must not silently treat a present-but-null key as
  equivalent to an absent one. This is a real behavior change from the
  version of this file first written for 12B.3C, which incorrectly claimed
  missing and null were always equivalent.
- Any single malformed entry inside `items` (e.g. missing the required
  `sku`, or any field holding an explicit `null`) fails Pydantic's
  list-item validation, which fails the *whole* response's validation —
  the client never silently drops a bad entry and reports a partial page
  as if it were complete.

If Amazon ever documents an actually-nullable field for this operation in
a future contract revision, that field should move back to plain `X |
None` (accepting null) with a citation of the exact schema text that says
so — nothing here should be read as a blanket "never accept null" policy,
only as "this pinned contract never asks for it."
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _reject_explicit_null(value: object) -> object:
    """Before-validator used only by `optional_not_null`. Pydantic does not
    validate a field's default value when its key is absent from the input
    (no `validate_default=True` is set anywhere here), so this function is
    only ever invoked when the key IS present in the input — exactly the
    case that must be rejected for a field the official schema documents as
    optional-but-never-null."""
    if value is None:
        raise ValueError(
            "the official schema documents this field as omittable, not nullable — "
            "an explicit JSON null is not a documented value"
        )
    return value


def optional_not_null(type_: type) -> type:
    """`optional_not_null(X)` means: Amazon may omit this key entirely
    (result: `None`, via the field's own `default=None`, never validated);
    if the key IS present, it must be a valid `X` — an explicit `null` is
    rejected. This is deliberately not the same as a plain `X | None`
    field, which would also accept an explicit `null` as valid input."""
    return Annotated[type_ | None, BeforeValidator(_reject_explicit_null)]


class ItemImage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    link: str
    height: int
    width: int


class ItemSummary(BaseModel):
    """One marketplace-scoped summary entry. V1 requests exactly one
    marketplace per call, so in practice this list should contain at most
    one entry — but it is modeled as the array the official schema defines,
    not flattened, since nothing here should assume Amazon's shape."""

    model_config = ConfigDict(extra="ignore")

    marketplace_id: str = Field(alias="marketplaceId")
    asin: optional_not_null(str) = None
    product_type: str = Field(alias="productType")
    condition_type: optional_not_null(str) = Field(default=None, alias="conditionType")
    status: list[str]
    fn_sku: optional_not_null(str) = Field(default=None, alias="fnSku")
    item_name: optional_not_null(str) = Field(default=None, alias="itemName")
    created_date: datetime = Field(alias="createdDate")
    last_updated_date: datetime = Field(alias="lastUpdatedDate")
    main_image: optional_not_null(ItemImage) = Field(default=None, alias="mainImage")


class IssueEnforcementAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str


class IssueExemption(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    expiry_date: optional_not_null(datetime) = Field(default=None, alias="expiryDate")


class IssueEnforcements(BaseModel):
    model_config = ConfigDict(extra="ignore")

    actions: list[IssueEnforcementAction]
    exemption: IssueExemption


class Issue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    severity: str
    attribute_names: optional_not_null(list[str]) = Field(default=None, alias="attributeNames")
    categories: list[str]
    enforcements: optional_not_null(IssueEnforcements) = None
    marketplace_ids: optional_not_null(list[str]) = Field(default=None, alias="marketplaceIds")


class Money(BaseModel):
    model_config = ConfigDict(extra="ignore")

    currency_code: str = Field(alias="currencyCode")
    amount: str


class Points(BaseModel):
    model_config = ConfigDict(extra="ignore")

    points_number: int = Field(alias="pointsNumber")


class Audience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: optional_not_null(str) = None
    display_name: optional_not_null(str) = Field(default=None, alias="displayName")


class ItemOffer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    marketplace_id: str = Field(alias="marketplaceId")
    offer_type: str = Field(alias="offerType")
    price: Money
    points: optional_not_null(Points) = None
    audience: optional_not_null(Audience) = None


class FulfillmentAvailability(BaseModel):
    """No `marketplaceId` field per the official schema — fulfillment
    availability is not itself marketplace-scoped. `quantity`'s `ge=0`
    mirrors the schema's own documented `"minimum": 0`."""

    model_config = ConfigDict(extra="ignore")

    fulfillment_channel_code: str = Field(alias="fulfillmentChannelCode")
    quantity: optional_not_null(int) = Field(default=None, ge=0)


class ItemProductType(BaseModel):
    model_config = ConfigDict(extra="ignore")

    marketplace_id: str = Field(alias="marketplaceId")
    product_type: str = Field(alias="productType")


class Item(BaseModel):
    """A single listings item. Only `sku` is required by the official
    schema; every other field is optional and independently omittable."""

    model_config = ConfigDict(extra="ignore")

    sku: str
    summaries: optional_not_null(list[ItemSummary]) = None
    issues: optional_not_null(list[Issue]) = None
    offers: optional_not_null(list[ItemOffer]) = None
    fulfillment_availability: optional_not_null(list[FulfillmentAvailability]) = Field(
        default=None, alias="fulfillmentAvailability"
    )
    product_types: optional_not_null(list[ItemProductType]) = Field(default=None, alias="productTypes")


class ListingsPagination(BaseModel):
    """Both fields are independently optional (may be absent). The official
    contract's documented end-of-pagination signal is the *absence* of the
    `nextToken` key, not an empty string and not an explicit `null` — an
    explicit `null` is not documented anywhere for this object and is
    rejected as malformed, the same as any other optional field in this
    contract."""

    model_config = ConfigDict(extra="ignore")

    next_token: optional_not_null(str) = Field(default=None, alias="nextToken")
    previous_token: optional_not_null(str) = Field(default=None, alias="previousToken")


class SearchListingsItemsResponse(BaseModel):
    """Matches Amazon's real `ItemSearchResults` schema exactly.

    `numberOfResults` and `items` are both required by the official schema
    (see the module docstring for why that matters); `pagination` is
    optional (absent-only, not nullable — see `ListingsPagination`).
    """

    model_config = ConfigDict(extra="ignore")

    number_of_results: int = Field(alias="numberOfResults")
    pagination: optional_not_null(ListingsPagination) = None
    items: list[Item]


class ListingsPageProvenance(BaseModel):
    """Non-secret connectivity metadata. Never include tokens, authorization
    headers, or raw response bodies. `rate_limit`/`request_id` come from
    HTTP headers, not the JSON body the nullability rules above apply to —
    a header is simply present-with-a-string-value or absent, so no
    null-vs-absent distinction applies to them. They are sanitized
    (length-bounded, control characters stripped) by the client before
    reaching this model, since a header value is attacker-influenced
    upstream data and this model's fields may eventually be persisted."""

    model_config = ConfigDict(extra="ignore")

    provider: str = "amazon_sp_api"
    api: str = "listings-items"
    operation: str
    region: str
    endpoint_host: str
    fetched_at: datetime
    http_status: int
    api_model_version: str
    attempt_count: int
    rate_limit: str | None = None
    request_id: str | None = None


class ListingsPage(BaseModel):
    """One fetched-and-parsed official API page. Deliberately does not carry
    the seller ID: the future orchestrator (12B.3D) already has it (it
    supplied it on the request) and does not need it echoed back here.
    `marketplace_id` always echoes the caller's *request*, never anything
    read from the response body — see `AmazonSpApiListingsClient._to_page`.
    """

    model_config = ConfigDict(extra="ignore")

    items: list[Item]
    number_of_results: int
    next_token: str | None
    marketplace_id: str
    page_token_used: str | None
    provenance: ListingsPageProvenance
