"""Safe parsing of Amazon export numbers.

Percentages are stored as fractions (0.145 == 14.5%).
Money uses Decimal. No currency conversion.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.000001")

EMPTY_TOKENS = {"", "-", "--", "—", "n/a", "na", "null", "none", "#n/a"}


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip().casefold()
    return text in EMPTY_TOKENS


def parse_int(value: Any) -> int | None:
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    text = _strip_numeric_noise(str(value))
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def parse_money(value: Any) -> Decimal | None:
    if is_blank(value):
        return None
    if isinstance(value, Decimal):
        return _quantize_money(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _quantize_money(Decimal(value))
    if isinstance(value, float):
        return _quantize_money(Decimal(str(value)))
    text = str(value).strip()
    text = (
        text.replace("₹", "")
        .replace("INR", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace("inr", "")
    )
    text = _strip_numeric_noise(text)
    if not text:
        return None
    try:
        return _quantize_money(Decimal(text))
    except InvalidOperation:
        return None


def parse_percentage(value: Any) -> Decimal | None:
    """Return a fraction in 0–1+ form.

    ``14.5%`` → 0.145
    ``0.145`` → 0.145
    ``14.5`` (no percent sign, greater than 1) → 0.145
    ``1`` / ``1.0`` → 1.0 (100%)
    """
    if is_blank(value):
        return None
    had_percent = False
    if isinstance(value, str):
        had_percent = "%" in value
        text = _strip_numeric_noise(value.replace("%", ""))
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    elif isinstance(value, bool):
        return None
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(str(value))
    else:
        return None

    if had_percent or number > 1:
        number = number / Decimal("100")
    return number.quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


def parse_text(value: Any) -> str | None:
    if is_blank(value):
        return None
    text = str(value).strip()
    return text or None


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _strip_numeric_noise(value: str) -> str:
    text = value.strip().replace(",", "").replace(" ", "")
    text = text.replace("\u00a0", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    return text
