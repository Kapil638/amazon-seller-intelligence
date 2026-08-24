"""Amazon OAuth callback intake helpers.

Validates documented redirect-URI query parameters. Token exchange and
SecretProvider storage live in `AmazonConnectionService`, not here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr

CallbackNotice = Literal["success", "denied", "error"]
CallbackOutcome = Literal["token_stored", "code_received", "denied", "invalid"]

AMAZON_ACCESS_DENIED = "access_denied"


class AuthorizationCodeReceived(BaseModel):
    """Internal callback result. Never includes the authorization code."""

    model_config = ConfigDict(extra="forbid")

    connection_id: str
    organization_id: str
    authorization_code_present: bool
    connection_status: str
    outcome: CallbackOutcome
    notice: CallbackNotice
    reason: str


def frontend_connection_return_url(origin: str, notice: CallbackNotice) -> str:
    base = origin.strip().rstrip("/")
    if notice not in ("success", "denied", "error"):
        notice = "error"
    return f"{base}/connection?amazon={notice}"


def wrap_authorization_code(*, spapi_oauth_code: str | None, code: str | None) -> SecretStr | None:
    """Hold the code in memory as SecretStr, then discard. Never log the value."""
    raw = (spapi_oauth_code or "").strip() or (code or "").strip()
    if not raw:
        return None
    return SecretStr(raw)


def is_amazon_access_denied(error: str | None) -> bool:
    return (error or "").strip().lower() == AMAZON_ACCESS_DENIED
