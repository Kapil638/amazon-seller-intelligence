from __future__ import annotations

import re
from datetime import datetime

from app.models.product import Price, Product
from app.reports.fonts import font_supports_rupee

_UNICODE_MAP = {
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
    "\u00ad": "",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u00a0": " ",
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    for src, dest in _UNICODE_MAP.items():
        text = text.replace(src, dest)
    if not font_supports_rupee():
        text = text.replace("₹", "Rs.")
    return text


def display_title(product: Product) -> tuple[str, str]:
    """Deterministic cover title: brand + recognizable remainder. Never calls AI."""
    full = (product.title or "").strip() or "Untitled listing"
    brand = (product.brand or "").strip()
    rest = full
    if brand and rest.lower().startswith(brand.lower()):
        rest = rest[len(brand) :].lstrip(" -|,/;")
    rest = rest.strip() or full
    if len(rest) > 88:
        window = rest[:88]
        if "," in window:
            rest = window.split(",", 1)[0].strip()
        else:
            rest = window.rsplit(" ", 1)[0].strip()
    return (brand or "Not available", rest)


def format_date_short(value: datetime | None) -> str:
    if value is None:
        return "Not available"
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value.strftime("%d %b %Y")


def format_date_long(value: datetime | None) -> str:
    if value is None:
        return "Not available"
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value.strftime("%d %b %Y, %H:%M")


def format_int(value: int | None) -> str:
    if value is None:
        return "Not available"
    return f"{value:,}"


def format_rating(value: float | None) -> str:
    if value is None:
        return "Not available"
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text} / 5"


def format_price(price: Price | None, marketplace: str | None = None) -> str:
    if price is None or price.amount is None:
        return "Not available"
    amount = float(price.amount)
    currency = (price.currency or "").upper()
    market = (marketplace or "").lower()
    if amount.is_integer():
        number = f"{int(amount):,}"
    else:
        number = f"{amount:,.2f}"
    if currency in {"INR", "RS", "INR ₹"} or market.endswith(".in") or market == "amazon.in":
        symbol = "₹" if font_supports_rupee() else "Rs."
        return f"{symbol}{number}"
    if currency in {"USD"}:
        return f"${number}"
    if currency in {"EUR"}:
        return f"EUR {number}"
    if currency in {"GBP"}:
        return f"GBP {number}"
    if currency:
        return f"{number} {currency}"
    return number


def split_assessment(text: str | None) -> list[str]:
    """Split persisted wording into readable paragraphs without changing meaning."""
    raw = (text or "").strip()
    if not raw:
        return []
    blocks = [part.strip() for part in re.split(r"\n\s*\n", raw) if part.strip()]
    if len(blocks) >= 2:
        return blocks
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", raw) if part.strip()]
    if len(sentences) <= 2:
        return [raw]
    grouped: list[str] = []
    for index in range(0, len(sentences), 2):
        grouped.append(" ".join(sentences[index : index + 2]))
    return grouped


def coverage_band(percentage: int) -> str:
    if percentage >= 85:
        return "High evidence coverage"
    if percentage >= 60:
        return "Moderate evidence coverage"
    return "Limited evidence coverage"


def evidence_label(state: str) -> str:
    mapping = {
        "observed": "Observed",
        "reported_absent": "Reported absent",
        "unknown": "Unknown",
    }
    return mapping.get(state, state.replace("_", " ").title())
