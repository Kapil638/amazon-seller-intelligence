"""12B.5A — Skill 3: Order and Sales Trend Analyst.

For one marketplace participation and period: orders, units, approved
order value by currency, fulfillment-status distribution, top/bottom
SKUs by units, and comparison against the immediately preceding
equal-length period (absolute and percentage change, with honest
zero-baseline behavior — never a fabricated "+inf%").

Deliberately calls the money metric "order value," never "revenue" or
"profit" — `order_total_amount`/`item_proceeds_amount` are exactly what
the stored contract means them to be (an order/item's own recorded
total), with no COGS, fees, or ad spend netted out anywhere in this
schema. Currencies are never summed together or converted.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.amazon.orders_read import AmazonOrdersReadService
from app.copilot.skills.contracts import SkillEvidence, incomplete_run, safe_deep_link
from app.copilot.skills.shared import CurrencySafeTotal, build_periods, percentage_change

TOP_BOTTOM_SKU_LIMIT = 5


class OrderTrendEvidenceService:
    """Deterministic evidence for the `analyze_order_trends` tool."""

    def __init__(self, orders: AmazonOrdersReadService | None = None) -> None:
        self._orders = orders or AmazonOrdersReadService()

    def analyze(self, marketplace_participation_id: UUID, *, period_days: int | None = None) -> SkillEvidence:
        analysis_period, comparison_period = build_periods(period_days)
        summary = self._orders.get_summary(marketplace_participation_id)

        current_rows = self._orders.list_order_items_for_window(
            marketplace_participation_id, created_after=analysis_period.start, created_before=analysis_period.end
        )
        previous_rows = self._orders.list_order_items_for_window(
            marketplace_participation_id,
            created_after=comparison_period.start,
            created_before=comparison_period.end,
        )

        current = _window_metrics(current_rows)
        previous = _window_metrics(previous_rows)

        order_count_change = percentage_change(current.order_count, previous.order_count)
        unit_count_change = percentage_change(current.units, previous.units)

        fulfillment_distribution: dict[str, int] = {}
        order_seen: set[UUID] = set()
        for row in current_rows:
            if row.order_id in order_seen:
                continue
            order_seen.add(row.order_id)
            key = row.order_fulfillment_status or "UNKNOWN"
            fulfillment_distribution[key] = fulfillment_distribution.get(key, 0) + 1

        units_by_sku: dict[str, int] = {}
        for row in current_rows:
            units_by_sku[row.seller_sku] = units_by_sku.get(row.seller_sku, 0) + row.quantity_ordered
        ranked_skus = sorted(units_by_sku.items(), key=lambda pair: (-pair[1], pair[0]))
        top_skus = ranked_skus[:TOP_BOTTOM_SKU_LIMIT]
        bottom_skus = ranked_skus[-TOP_BOTTOM_SKU_LIMIT:] if len(ranked_skus) > TOP_BOTTOM_SKU_LIMIT else []

        # `list_order_items_for_window` is an inner join — an order with
        # zero item rows can never appear in `current_rows` at all, so
        # "unmatched order-item coverage" has to be measured against the
        # authoritative, order-level count instead (one cheap `limit=1`
        # call for its `.total`, not a second full row fetch).
        period_order_total = self._orders.list_orders(
            marketplace_participation_id,
            created_after=analysis_period.start,
            created_before=analysis_period.end,
            limit=1,
        ).total
        orders_without_items = max(period_order_total - current.order_count, 0)

        limitations = [
            'Reflects order_total_amount/item_proceeds_amount as stored, never "revenue" or "profit" — '
            "no COGS, fees, or ad spend is netted out anywhere in this schema.",
            "Cannot show activity before this account's ingestion lookback ceiling.",
            "Currencies are never summed together or converted.",
        ]
        if orders_without_items:
            limitations.append(
                f"{orders_without_items} order(s) in this window have no item rows on record."
            )

        freshness_incomplete = incomplete_run(summary.sync.status)

        return SkillEvidence(
            skill_id="order_and_sales_trend_analyst",
            skill_version="1.0.0",
            organization_id=_org_id(),
            marketplace_participation_ids=[marketplace_participation_id],
            analysis_period=analysis_period,
            comparison_period=comparison_period,
            orders_freshness=summary.sync,
            has_newer_incomplete_run=freshness_incomplete,
            metrics={
                "order_count": current.order_count,
                "order_count_previous_period": previous.order_count,
                "order_count_percentage_change": order_count_change,
                "unit_count": current.units,
                "unit_count_previous_period": previous.units,
                "unit_count_percentage_change": unit_count_change,
                "order_value_by_currency": current.value.as_dict(),
                "order_value_previous_period_by_currency": previous.value.as_dict(),
                "fulfillment_status_distribution": fulfillment_distribution,
                "orders_without_items_count": orders_without_items,
            },
            records=[
                {"kind": "top_sku_by_units", "seller_sku": sku, "units": units} for sku, units in top_skus
            ]
            + [
                {"kind": "bottom_sku_by_units", "seller_sku": sku, "units": units}
                for sku, units in bottom_skus
            ],
            limitations=limitations,
            confidence="insufficient_data" if current.order_count == 0 else (
                "medium" if freshness_incomplete else "high"
            ),
            deep_links=[
                safe_deep_link(
                    f"/seller/orders?participation={marketplace_participation_id}",
                    "View orders for this marketplace",
                )
            ],
        )


class _WindowMetrics:
    __slots__ = ("order_count", "units", "value")

    def __init__(self, order_count: int, units: int, value: CurrencySafeTotal) -> None:
        self.order_count = order_count
        self.units = units
        self.value = value


def _window_metrics(rows) -> _WindowMetrics:
    order_ids: set[UUID] = set()
    units = 0
    value = CurrencySafeTotal()
    seen_orders_for_value: set[UUID] = set()
    for row in rows:
        order_ids.add(row.order_id)
        units += row.quantity_ordered
        # Order value is an ORDER-level total (order_total_amount), not a
        # sum of per-item proceeds — add it once per distinct order, not
        # once per item, or a multi-item order would be double-counted.
        if row.order_id not in seen_orders_for_value:
            seen_orders_for_value.add(row.order_id)
            value.add(row.order_total_amount, row.order_total_currency)
    return _WindowMetrics(order_count=len(order_ids), units=units, value=value)


def _org_id() -> UUID:
    from app.persistence.database import current_organization_id

    return current_organization_id()
