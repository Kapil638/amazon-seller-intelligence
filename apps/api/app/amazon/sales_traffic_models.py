"""External-provider DTOs for the Sales and Traffic Business Report
(`GET_SALES_AND_TRAFFIC_REPORT`, schema `sellerSalesAndTrafficReport.json`).
Not the ASI canonical data model — see `app.persistence.models.
AmazonSalesAndTrafficDailyFact`/`AmazonSalesAndTrafficProductFact` for that.

Pinned against `docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md` §1, fetched
directly from `amzn/selling-partner-api-models`
(`schemas/reports/sellerSalesAndTrafficReport.json`, `main` branch) during
this milestone.

Every model uses `model_config = ConfigDict(extra="ignore", alias_generator
=to_camel, populate_by_name=True)` — the same `extra="ignore"` posture
`orders_models.py` already established for external Amazon response
parsing (an undeclared key is silently dropped during `model_validate()`
and never becomes a Python attribute, so it can never reach a log line,
an exception, or persistence through this module), plus a model-wide
camelCase alias generator rather than `orders_models.py`'s per-field
`Field(alias=...)` — a deliberate deviation from that file's own
convention, made because this report's field count (~120 across every
`_b2b` variant) makes ~120 hand-written aliases a materially higher
transcription-error risk than one generator applied uniformly, verified
directly to produce the exact casing Amazon's own contract uses even for
the `B2B` suffix (`ordered_product_sales_b2b` -> `orderedProductSalesB2B`,
checked against `pydantic.alias_generators.to_camel` directly before
committing to this approach).

This report's own contract contains no buyer/order PII at all (it is
exclusively seller-aggregate performance data, handover doc §2), so there
is no "deliberately excluded PII field" list to maintain here — every
field the pinned schema defines is declared.

**Grain, never invented here** (handover doc §1a): `SalesAndTrafficByAsin`
has no `date` field — this module does not add one. Callers must supply
the request's own `dataStartTime`/`dataEndTime` window separately when
persisting an ASIN-level row.

Percentage and money fields are typed `Decimal`, never `float` — Pydantic
coerces a JSON number directly to `Decimal` via its string representation,
never through an intermediate Python `float`, so a value like `12.08`
round-trips exactly rather than picking up binary-float representation
error before it ever reaches a `Numeric` database column.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

_MODEL_CONFIG = ConfigDict(extra="ignore", alias_generator=to_camel, populate_by_name=True)


class Amount(BaseModel):
    model_config = _MODEL_CONFIG

    amount: Decimal
    currency_code: str


class SalesByDate(BaseModel):
    model_config = _MODEL_CONFIG

    ordered_product_sales: Amount
    ordered_product_sales_b2b: Amount | None = None
    units_ordered: int
    units_ordered_b2b: int | None = None
    total_order_items: int
    total_order_items_b2b: int | None = None
    average_sales_per_order_item: Amount
    average_sales_per_order_item_b2b: Amount | None = None
    average_units_per_order_item: Decimal
    average_units_per_order_item_b2b: Decimal | None = None
    average_selling_price: Amount
    average_selling_price_b2b: Amount | None = None
    units_refunded: int
    refund_rate: Decimal
    claims_granted: int
    claims_amount: Amount
    shipped_product_sales: Amount
    units_shipped: int
    orders_shipped: int


class TrafficByDate(BaseModel):
    model_config = _MODEL_CONFIG

    browser_page_views: int
    browser_page_views_b2b: int | None = None
    mobile_app_page_views: int
    mobile_app_page_views_b2b: int | None = None
    page_views: int
    page_views_b2b: int | None = None
    browser_sessions: int
    browser_sessions_b2b: int | None = None
    mobile_app_sessions: int
    mobile_app_sessions_b2b: int | None = None
    sessions: int
    sessions_b2b: int | None = None
    buy_box_percentage: Decimal
    buy_box_percentage_b2b: Decimal | None = None
    order_item_session_percentage: Decimal
    order_item_session_percentage_b2b: Decimal | None = None
    # No upper-bound validation here — the pinned contract's own schema
    # leaves this field unbounded above (handover doc §3).
    unit_session_percentage: Decimal
    unit_session_percentage_b2b: Decimal | None = None
    average_offer_count: int
    average_parent_items: int
    feedback_received: int
    negative_feedback_received: int
    received_negative_feedback_rate: Decimal


class SalesAndTrafficByDate(BaseModel):
    model_config = _MODEL_CONFIG

    date: str  # bare `YYYY-MM-DD`, no timezone offset in the pinned contract
    sales_by_date: SalesByDate
    traffic_by_date: TrafficByDate


class SalesByAsin(BaseModel):
    model_config = _MODEL_CONFIG

    units_ordered: int
    units_ordered_b2b: int | None = None
    ordered_product_sales: Amount
    ordered_product_sales_b2b: Amount | None = None
    total_order_items: int
    total_order_items_b2b: int | None = None


class TrafficByAsin(BaseModel):
    model_config = _MODEL_CONFIG

    browser_sessions: int
    browser_sessions_b2b: int | None = None
    mobile_app_sessions: int
    mobile_app_sessions_b2b: int | None = None
    sessions: int
    sessions_b2b: int | None = None
    browser_session_percentage: Decimal
    browser_session_percentage_b2b: Decimal | None = None
    mobile_app_session_percentage: Decimal
    mobile_app_session_percentage_b2b: Decimal | None = None
    session_percentage: Decimal
    session_percentage_b2b: Decimal | None = None
    browser_page_views: int
    browser_page_views_b2b: int | None = None
    mobile_app_page_views: int
    mobile_app_page_views_b2b: int | None = None
    page_views: int
    page_views_b2b: int | None = None
    browser_page_views_percentage: Decimal
    browser_page_views_percentage_b2b: Decimal | None = None
    mobile_app_page_views_percentage: Decimal
    mobile_app_page_views_percentage_b2b: Decimal | None = None
    page_views_percentage: Decimal
    page_views_percentage_b2b: Decimal | None = None
    buy_box_percentage: Decimal
    buy_box_percentage_b2b: Decimal | None = None
    # No upper-bound validation — see TrafficByDate.unit_session_percentage.
    unit_session_percentage: Decimal
    unit_session_percentage_b2b: Decimal | None = None


class SalesAndTrafficByAsin(BaseModel):
    """Deliberately no `date` field — see module docstring's grain note."""

    model_config = _MODEL_CONFIG

    parent_asin: str
    child_asin: str | None = None
    sku: str | None = None
    sales_by_asin: SalesByAsin
    traffic_by_asin: TrafficByAsin


class ReportSpecification(BaseModel):
    model_config = _MODEL_CONFIG

    report_type: str
    data_start_time: str
    data_end_time: str
    marketplace_ids: list[str]


class SalesAndTrafficReport(BaseModel):
    """Top-level parsed report document. `extra="ignore"` at every level
    (see module docstring) is what lets this model tolerate a genuinely
    unknown future field without raising — proven by this milestone's own
    "unknown future field" synthetic fixture."""

    model_config = _MODEL_CONFIG

    report_specification: ReportSpecification
    sales_and_traffic_by_date: list[SalesAndTrafficByDate]
    sales_and_traffic_by_asin: list[SalesAndTrafficByAsin]
