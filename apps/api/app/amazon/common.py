"""Shared Amazon-source identities. Do not collapse these into one generic provider.

Rainforest remains marketplace intelligence (`app.providers.rainforest`).
SP-API is seller-owned intelligence (`app.amazon`).
Ads API is a future advertising collection source, not implemented here.
"""

from __future__ import annotations

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


def contains_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
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
