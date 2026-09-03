"""12B.5A — helpers shared by all five skill evidence services.

Kept deliberately tiny and dependency-free (no ORM imports) so every
skill can reuse the exact same currency-safety and period-window
behavior without importing from each other.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypeVar

from app.copilot.skills.contracts import PeriodWindow

DEFAULT_PERIOD_DAYS = 30
MAX_PERIOD_DAYS = 90
# Defensive ceiling on how many pages any skill will walk through a
# paginated read-service response — a seller's real catalog/order volume
# today is tiny (single digits to low hundreds), but this keeps one
# skill call from turning into an unbounded loop if that ever changes.
MAX_PAGES = 20

_T = TypeVar("_T")


def clamp_period_days(period_days: int | None) -> int:
    """Never trust a caller-supplied window past a safe maximum — this is
    exactly the "date ranges have safe maximums and defaults" requirement
    (Phase 4), enforced once here rather than duplicated per skill."""
    if period_days is None:
        return DEFAULT_PERIOD_DAYS
    return max(1, min(period_days, MAX_PERIOD_DAYS))


def build_periods(period_days: int | None, *, now: datetime | None = None) -> tuple[PeriodWindow, PeriodWindow]:
    """The analysis period (the last `period_days` days up to now) and the
    immediately preceding, equal-length comparison period — the exact
    windowing every skill that compares periods uses, so "equal-length"
    and "immediately preceding" are guaranteed true by construction, not
    by each skill re-deriving the arithmetic."""
    days = clamp_period_days(period_days)
    end = now or datetime.now(UTC)
    start = end - timedelta(days=days)
    comparison_end = start
    comparison_start = comparison_end - timedelta(days=days)
    analysis = PeriodWindow(start=start, end=end, label=f"last {days} days")
    comparison = PeriodWindow(start=comparison_start, end=comparison_end, label=f"previous {days} days")
    return analysis, comparison


@dataclass
class CurrencySafeTotal:
    """Sums money strictly per currency — a value with no currency, or a
    currency ASI has never seen an amount for, is dropped from the total
    rather than silently attributed to an arbitrary bucket. Never
    performs conversion; never blends two currencies into one number."""

    totals: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    skipped_missing_currency_or_amount: int = 0

    def add(self, amount: Decimal | None, currency: str | None) -> None:
        if amount is None or not currency:
            self.skipped_missing_currency_or_amount += 1
            return
        self.totals[currency] += amount

    def as_dict(self) -> dict[str, str]:
        """String-keyed, string-valued (JSON-safe, exact decimal
        round-trip) — never a single blended float."""
        return {currency: str(total) for currency, total in sorted(self.totals.items())}

    def is_single_currency(self) -> bool:
        return len(self.totals) == 1

    def single_currency_or_none(self) -> tuple[Decimal | None, str | None]:
        if not self.is_single_currency():
            return None, None
        ((currency, total),) = self.totals.items()
        return total, currency


def fetch_all_pages(
    fetch_page: Callable[[int, int], tuple[list[_T], int]],
    *,
    page_size: int = 100,
    max_pages: int = MAX_PAGES,
) -> list[_T]:
    """Walks a `(offset, limit) -> (items, total)` paginated read-service
    method to completion, bounded by `max_pages`. Shared by every skill
    that needs "every row in this participation," not just one page —
    `list_listings`/`list_orders` themselves stay page-bounded (their
    existing, already-reviewed contract); this only changes how many
    times a skill calls them, never what either method returns."""
    items: list[_T] = []
    offset = 0
    for _ in range(max_pages):
        page_items, total = fetch_page(offset, page_size)
        items.extend(page_items)
        offset += page_size
        if not page_items or offset >= total:
            break
    return items


def percentage_change(current: float | int, previous: float | int) -> float | None:
    """`None` on a zero baseline — never a divide-by-zero, never a
    fabricated "+inf%". Callers must render `None` as "new activity" or
    similar, never as a number."""
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100.0
