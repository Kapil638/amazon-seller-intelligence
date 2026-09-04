"""12B.5A — Skill 4: Cancellation/Operational Anomaly Detector.

**Schema-reality correction from the original skill-matrix scenario**
(documented here, and in the skill matrix doc's own implementation-note
addendum, not silently): `docs/AI_HANDOVER/
LISTINGS_ORDERS_COPILOT_SKILL_MATRIX.md`'s Scenario 12 envisioned
grouping cancelled *items* by a `cancel_requester`/`cancelled_by` field
and an item-level `was_cancelled` flag. Neither exists on
`amazon_seller_order_items` (12B.4B's privacy-reviewed schema — see
`AmazonSellerOrderItem` in `app/persistence/models.py`) — only
`amazon_seller_orders.was_cancelled` (order-level) exists. Amazon's
cancellation requester/reason is not part of this schema at all, so this
skill computes cancellation count/rate strictly at *order* granularity
and reports "SKUs present on cancelled orders" (a proxy — presence on a
cancelled order, not proof every unit on it was itself cancelled), never
a requester or reason.

Anomaly labeling is a documented, configurable, tested threshold rule —
never a bare rate comparison. See `is_anomalous()`.

**Material fix (skill_version 1.0.0 -> 1.1.0):** `records` previously
listed *every* distinct SKU present on a cancelled order in the window,
with no limit at all — a seller with an extreme number of distinct
cancelled-order SKUs in one window could produce an unbounded evidence
payload, unlike every other one of the five skills, which all cap their
per-record output. `AFFECTED_SKU_LIMIT = 25` (matching Listing Health
Prioritizer's and Listing Risk by Order Exposure's own default result
limit) now bounds `records` to the top-N SKUs by how many cancelled
orders each was present on — deterministic, tied-broken by
`seller_sku` — while `affected_sku_count` (the full matching
population), `returned_sku_count`, and `sku_list_truncated` make the
truncation itself explicit rather than silent. Every aggregate
cancellation metric (`total_orders`, `cancelled_orders`,
`cancellation_rate`, the previous-period comparison, `is_anomalous`)
is computed from the full, untruncated order-level query this skill
already ran — never from the truncated SKU list — so bounding the
*records* never changes what the skill can honestly claim about the
*population*.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.amazon.orders_read import AmazonOrdersReadService
from app.copilot.skills.contracts import SKILL_VERSIONS, SkillEvidence, incomplete_run, safe_deep_link
from app.copilot.skills.shared import build_periods, percentage_change

# Configurable, documented, tested (see tests/test_copilot_skills_evidence.py).
# Below this many orders in the analysis period, a rate change is reported
# as-is but never labeled "anomalous" — too small a sample to be
# meaningful (Phase 3's explicit requirement).
MIN_SAMPLE_SIZE = 10
# An anomaly requires *both* enough volume (MIN_SAMPLE_SIZE) and either:
#   - the comparison-period rate was 0 and the current rate has reached
#     ANOMALY_FLOOR_RATE, or
#   - the current rate is at least ANOMALY_RELATIVE_INCREASE times the
#     comparison-period rate (a 50%+ relative increase, by default).
ANOMALY_RELATIVE_INCREASE = 1.5
ANOMALY_FLOOR_RATE = 0.10
# Matches Listing Health Prioritizer's/Listing Risk by Order Exposure's
# own `DEFAULT_RESULT_LIMIT` — the same "bounded top-N, never an
# unbounded per-record list" ceiling applied here to affected SKUs.
AFFECTED_SKU_LIMIT = 25
_SKILL_ID = "cancellation_operational_anomaly_detector"


@dataclass(frozen=True)
class _WindowCancellation:
    total_orders: int
    cancelled_orders: int

    @property
    def rate(self) -> float | None:
        if self.total_orders == 0:
            return None
        return self.cancelled_orders / self.total_orders


def is_anomalous(current: _WindowCancellation, previous: _WindowCancellation) -> tuple[bool, str]:
    """Returns `(is_anomalous, reason)`. `reason` is always populated,
    even when `is_anomalous` is False, so the evidence can state
    truthfully *why* something was or was not called an anomaly."""
    if current.total_orders < MIN_SAMPLE_SIZE:
        return False, f"sample too small ({current.total_orders} orders < minimum {MIN_SAMPLE_SIZE})"
    current_rate = current.rate or 0.0
    previous_rate = previous.rate
    if previous_rate is None or previous_rate == 0:
        if current_rate >= ANOMALY_FLOOR_RATE:
            return True, (
                f"no cancellations in the comparison period; current rate {current_rate:.1%} "
                f"reached the {ANOMALY_FLOOR_RATE:.0%} floor"
            )
        return False, f"current rate {current_rate:.1%} is below the {ANOMALY_FLOOR_RATE:.0%} floor"
    if current_rate >= previous_rate * ANOMALY_RELATIVE_INCREASE:
        return True, (
            f"current rate {current_rate:.1%} is at least {ANOMALY_RELATIVE_INCREASE}x "
            f"the comparison-period rate {previous_rate:.1%}"
        )
    return False, (
        f"current rate {current_rate:.1%} is not at least {ANOMALY_RELATIVE_INCREASE}x "
        f"the comparison-period rate {previous_rate:.1%}"
    )


class CancellationAnomalyEvidenceService:
    """Deterministic evidence for the `detect_cancellation_anomalies` tool."""

    def __init__(self, orders: AmazonOrdersReadService | None = None) -> None:
        self._orders = orders or AmazonOrdersReadService()

    def detect(self, marketplace_participation_id: UUID, *, period_days: int | None = None) -> SkillEvidence:
        analysis_period, comparison_period = build_periods(period_days)
        summary = self._orders.get_summary(marketplace_participation_id)

        current = self._window_cancellation(marketplace_participation_id, analysis_period)
        previous = self._window_cancellation(marketplace_participation_id, comparison_period)
        anomalous, reason = is_anomalous(current, previous)

        rate_change = (
            percentage_change(current.rate, previous.rate)
            if current.rate is not None and previous.rate is not None
            else None
        )

        affected_sku_counts = self._affected_sku_cancelled_order_counts(marketplace_participation_id, analysis_period)
        ranked_skus = sorted(affected_sku_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        top_skus = ranked_skus[:AFFECTED_SKU_LIMIT]
        sku_list_truncated = len(ranked_skus) > len(top_skus)

        limitations = [
            "Cannot explain why an order was cancelled — no free-text reason is stored, only the "
            "order-level was_cancelled flag.",
            "Cannot attribute cancellation to a specific requester (seller/buyer/Amazon) — that field "
            "does not exist in this schema.",
            "Affected SKUs are SKUs present on cancelled orders, not proof every unit on that order "
            "was itself cancelled (no item-level cancellation flag exists).",
        ]
        if current.total_orders < MIN_SAMPLE_SIZE:
            limitations.append(
                f"Only {current.total_orders} order(s) in this window — too small a sample to call "
                "any change anomalous."
            )
        if sku_list_truncated:
            limitations.append(
                f"Showing the top {len(top_skus)} of {len(ranked_skus)} affected SKUs, ranked by how "
                "many cancelled orders each appeared on — this is a prioritized subset, not the full "
                "affected-SKU population. Every cancellation count/rate above still reflects the full "
                "population, not just the SKUs shown here."
            )

        freshness_incomplete = incomplete_run(summary.sync.status)

        return SkillEvidence(
            skill_id=_SKILL_ID,
            skill_version=SKILL_VERSIONS[_SKILL_ID],
            organization_id=_org_id(),
            marketplace_participation_ids=[marketplace_participation_id],
            analysis_period=analysis_period,
            comparison_period=comparison_period,
            orders_freshness=summary.sync,
            has_newer_incomplete_run=freshness_incomplete,
            metrics={
                "total_orders": current.total_orders,
                "cancelled_orders": current.cancelled_orders,
                "cancellation_rate": current.rate,
                "total_orders_previous_period": previous.total_orders,
                "cancelled_orders_previous_period": previous.cancelled_orders,
                "cancellation_rate_previous_period": previous.rate,
                "cancellation_rate_percentage_change": rate_change,
                "is_anomalous": anomalous,
                "anomaly_reason": reason,
                "minimum_sample_size": MIN_SAMPLE_SIZE,
                "anomaly_relative_increase_threshold": ANOMALY_RELATIVE_INCREASE,
                "anomaly_floor_rate": ANOMALY_FLOOR_RATE,
                # 12B.5B remediation (bounded evidence): the full
                # matching population and how much of it is actually
                # returned — never calculable only from `records` once
                # `records` itself is a truncated top-N subset.
                "affected_sku_count": len(ranked_skus),
                "returned_sku_count": len(top_skus),
                "sku_list_truncated": sku_list_truncated,
            },
            records=[
                {"kind": "sku_on_cancelled_order", "seller_sku": sku, "cancelled_order_count": count}
                for sku, count in top_skus
            ],
            limitations=limitations,
            confidence="insufficient_data" if current.total_orders == 0 else (
                "medium" if freshness_incomplete else "high"
            ),
            deep_links=[
                safe_deep_link(
                    f"/seller/orders?participation={marketplace_participation_id}&fulfillment_status=CANCELLED",
                    "View cancelled orders",
                )
            ],
        )

    def _window_cancellation(self, marketplace_participation_id: UUID, period) -> _WindowCancellation:
        total = self._orders.list_orders(
            marketplace_participation_id, created_after=period.start, created_before=period.end, limit=1
        ).total
        cancelled = self._orders.list_orders(
            marketplace_participation_id,
            created_after=period.start,
            created_before=period.end,
            fulfillment_status="CANCELLED",
            limit=1,
        ).total
        return _WindowCancellation(total_orders=total, cancelled_orders=cancelled)

    def _affected_sku_cancelled_order_counts(self, marketplace_participation_id: UUID, period) -> dict[str, int]:
        """Every distinct SKU present on a cancelled order in this
        window, mapped to how many *distinct cancelled orders* it
        appeared on (never a raw item-row count, which could double-
        count a SKU repeated on one order) — this count is exactly what
        the top-N ranking in `detect()` sorts by, and is itself
        returned per-record (`cancelled_order_count`) so the ranking is
        never opaque."""
        rows = self._orders.list_order_items_for_window(
            marketplace_participation_id, created_after=period.start, created_before=period.end
        )
        orders_by_sku: dict[str, set] = {}
        for row in rows:
            if not row.order_was_cancelled:
                continue
            orders_by_sku.setdefault(row.seller_sku, set()).add(row.order_id)
        return {sku: len(order_ids) for sku, order_ids in orders_by_sku.items()}


def _org_id() -> UUID:
    from app.persistence.database import current_organization_id

    return current_organization_id()
