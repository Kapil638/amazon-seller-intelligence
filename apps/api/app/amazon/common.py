"""Shared Amazon-source identities. Do not collapse these into one generic provider.

Rainforest remains marketplace intelligence (`app.providers.rainforest`).
SP-API is seller-owned intelligence (`app.amazon`).
Ads API is a future advertising collection source, not implemented here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

PROVIDER_SP_API = "SP_API"
PROVIDER_ADS_API = "ADS_API"
PROVIDER_RAINFOREST = "RAINFOREST"

ENVIRONMENT_SANDBOX = "SANDBOX"
ENVIRONMENT_PRODUCTION = "PRODUCTION"

SECRET_KEY_FRAGMENTS = (
    "token",
    "secret",
    "authorization",
    "password",
    "api_key",
    "apikey",
    "x_amz_access",
)

# Public JSON keys that contain a secret fragment but are not credentials.
# `authorization_url` is the Seller Central consent URL (12B.1C.2), not an
# Authorization header or authorization_code.
# `authorization_code_present` is a boolean flag (12B.1C.4A), not the code.
PUBLIC_KEY_ALLOWLIST = frozenset({"authorization_url", "authorization_code_present"})


def ensure_utc(value: datetime) -> datetime:
    """Every `DateTime(timezone=True)` column in this codebase is written
    and reasoned about as UTC (see `AmazonIngestionRun`'s docstring in
    `models.py`), but SQLite — used throughout this test suite — silently
    drops timezone info on round-trip, returning a naive `datetime` for a
    value that was genuinely written as UTC. PostgreSQL (production)
    returns it already tz-aware. This makes arithmetic against
    `datetime.now(UTC)` safe regardless of which backend produced the
    value, without ever reinterpreting a value's actual instant in time."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def contains_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in PUBLIC_KEY_ALLOWLIST:
        return False
    return any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS)


def reject_secret_fields(value: Any, path: str = "root") -> None:
    """Raise if a public payload would include credential-shaped keys."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if contains_secret_key(str(key)):
                raise RuntimeError(f"Refusing to serialize secret field {child_path}")
            reject_secret_fields(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_fields(child, f"{path}[{index}]")


def public_model_dump(model: BaseModel) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    reject_secret_fields(payload)
    return payload
