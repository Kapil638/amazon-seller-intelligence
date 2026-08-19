"""Central Amazon report header normalization and field aliases.

Amazon column names vary by marketplace, export version, and date window.
All alias matching happens here so parsers do not scatter header logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_header(value: str) -> str:
    lowered = value.replace("\ufeff", "").strip().casefold()
    lowered = lowered.replace("%", " percent ")
    lowered = lowered.replace("#", " ")
    lowered = lowered.replace("(", " ").replace(")", " ")
    return " ".join(NON_ALNUM_RE.sub(" ", lowered).split())


@dataclass(frozen=True)
class FieldSpec:
    internal: str
    aliases: tuple[str, ...]
    display_name: str
    distinctive: bool = False


# Aliases are tried in order. Prefer 7-day windows over 14/30 when both exist.
SEARCH_TERM_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("date", ("date", "day"), "Date"),
    FieldSpec(
        "campaign_name",
        ("campaign name", "campaign", "campaigns"),
        "Campaign Name",
        distinctive=True,
    ),
    FieldSpec("campaign_id", ("campaign id", "campaign identifier"), "Campaign ID"),
    FieldSpec(
        "ad_group_name",
        ("ad group name", "ad group", "adgroup name", "adgroup"),
        "Ad Group Name",
    ),
    FieldSpec("ad_group_id", ("ad group id", "adgroup id"), "Ad Group ID"),
    FieldSpec("targeting", ("targeting", "targeting type"), "Targeting", distinctive=True),
    FieldSpec("match_type", ("match type", "match"), "Match Type", distinctive=True),
    FieldSpec(
        "customer_search_term",
        (
            "customer search term",
            "customer search terms",
            "search term",
            "search terms",
            "keyword text",
        ),
        "Customer Search Term",
        distinctive=True,
    ),
    FieldSpec("impressions", ("impressions", "impression"), "Impressions", distinctive=True),
    FieldSpec("clicks", ("clicks", "click"), "Clicks"),
    FieldSpec(
        "spend",
        ("spend", "cost", "total spend", "ad spend"),
        "Spend",
        distinctive=True,
    ),
    FieldSpec(
        "sales",
        (
            "7 day total sales",
            "14 day total sales",
            "30 day total sales",
            "total sales",
            "sales",
            "7 day advertised sku sales",
            "14 day advertised sku sales",
            "30 day advertised sku sales",
        ),
        "Sales",
    ),
    FieldSpec(
        "orders",
        (
            "7 day total orders",
            "14 day total orders",
            "30 day total orders",
            "total orders",
            "orders",
            "7 day advertised sku units",
        ),
        "Orders",
    ),
    FieldSpec(
        "units",
        (
            "7 day total units",
            "14 day total units",
            "30 day total units",
            "total units",
            "units",
        ),
        "Units",
    ),
    FieldSpec("currency", ("currency", "currency code"), "Currency"),
)

SEARCH_TERM_REQUIRED = (
    "customer_search_term",
    "impressions",
    "clicks",
    "spend",
    "sales",
    "orders",
)

BUSINESS_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("date", ("date", "day"), "Date"),
    FieldSpec("parent_asin", ("parent asin", "parent"), "Parent ASIN"),
    FieldSpec(
        "asin",
        ("child asin", "asin", "child"),
        "(Child) ASIN",
        distinctive=True,
    ),
    FieldSpec("sku", ("sku", "seller sku", "merchant sku"), "SKU"),
    FieldSpec("title", ("title", "product title", "item name", "product name"), "Title"),
    FieldSpec(
        "sessions",
        ("sessions", "session", "sessions total", "total sessions"),
        "Sessions",
        distinctive=True,
    ),
    FieldSpec(
        "page_views",
        ("page views", "page view", "pageviews", "page views total"),
        "Page Views",
        distinctive=True,
    ),
    FieldSpec(
        "buy_box_percentage",
        (
            "buy box percentage",
            "buy box percent",
            "featured offer buy box percentage",
            "featured offer buy box percent",
            "buy box",
        ),
        "Buy Box Percentage",
        distinctive=True,
    ),
    FieldSpec(
        "units_ordered",
        ("units ordered", "units", "unit ordered"),
        "Units Ordered",
        distinctive=True,
    ),
    FieldSpec(
        "ordered_product_sales",
        (
            "ordered product sales",
            "ordered product sale",
            "product sales",
            "sales",
        ),
        "Ordered Product Sales",
    ),
    FieldSpec(
        "unit_session_percentage",
        (
            "unit session percentage",
            "unit session percent",
            "unit session rate",
            "conversion rate",
        ),
        "Unit Session Percentage",
        distinctive=True,
    ),
)

BUSINESS_REQUIRED = ("asin", "sessions")
BUSINESS_REQUIRED_ONE_OF = (("units_ordered", "ordered_product_sales"),)

# Distinctive fields used only for type detection (not all mapped columns).
SEARCH_TERM_DISTINCTIVE = tuple(spec.internal for spec in SEARCH_TERM_FIELDS if spec.distinctive)
BUSINESS_DISTINCTIVE = tuple(spec.internal for spec in BUSINESS_FIELDS if spec.distinctive)

ALL_KNOWN_NORMALIZED = {
    normalize_header(alias)
    for spec in (*SEARCH_TERM_FIELDS, *BUSINESS_FIELDS)
    for alias in spec.aliases
}


def map_headers(headers: list[str], specs: tuple[FieldSpec, ...]) -> dict[str, str]:
    """Map internal field → original header. First matching alias wins."""
    normalized_to_original: dict[str, str] = {}
    for header in headers:
        key = normalize_header(header)
        if key and key not in normalized_to_original:
            normalized_to_original[key] = header.strip()

    mapped: dict[str, str] = {}
    used_originals: set[str] = set()
    for spec in specs:
        for alias in spec.aliases:
            original = normalized_to_original.get(normalize_header(alias))
            if original is None or original in used_originals:
                continue
            mapped[spec.internal] = original
            used_originals.add(original)
            break
    return mapped


def display_names(internal_fields: list[str], specs: tuple[FieldSpec, ...]) -> list[str]:
    lookup = {spec.internal: spec.display_name for spec in specs}
    return [lookup.get(name, name) for name in internal_fields]
