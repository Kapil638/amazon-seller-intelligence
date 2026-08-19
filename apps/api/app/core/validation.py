import re

ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")


def normalize_asin(asin: str) -> str:
    return asin.strip().upper()


def is_valid_asin(asin: str) -> bool:
    return bool(ASIN_PATTERN.fullmatch(asin))
