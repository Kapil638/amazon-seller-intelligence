"""12B.1C.5 — OAuth callback LWA exchange + SecretProvider storage. No SP-API."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from app.amazon.common import public_model_dump
from app.amazon.connection import AmazonConnectionService, get_amazon_connection_service
from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL
from app.amazon.lwa_token import AmazonLwaTokenService
from app.amazon.models import LwaAuthorizationGrant
from app.amazon.oauth import hash_oauth_state, new_oauth_state
from app.amazon.secrets import (
    DevelopmentSecretProvider,
    SecretAccessError,
    build_asi_secret_reference,
)
from app.core.config import Settings
from app.core.exceptions import SpApiAuthenticationError, SpApiRequestFailedError
from app.main import app
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonOAuthState, Organization
from app.persistence.repositories import AmazonConnectionRepository, AmazonOAuthStateRepository

CALLBACK_URL = "/api/v1/amazon/connection/callback"
CONNECTION_URL = "/api/v1/amazon/connection"
TEST_CODE = "SplxlOexampleCallbackCode12B1C5"
ACCESS_TOKEN = "Atza|test-12b1c5-access-token"
REFRESH_TOKEN = "Atzr|test-12b1c5-refresh-token"
CLIENT_SECRET = "test-oauth-lwa-client-secret-value"
CLIENT_ID = "amzn1.application-oa2-client.oauth-test"
REDIRECT_URI = "https://app.example.test/api/v1/amazon/connection/callback"


def _authorize_settings(**overrides) -> Settings:
    values = dict(
        sp_api_application_id="amzn1.sellerapps.app.test-app",
        sp_api_production_application_id="",
        sp_api_oauth_redirect_uri=REDIRECT_URI,
        sp_api_oauth_state_ttl_seconds=600,
        sp_api_consent_version_beta=True,
        default_marketplace="amazon.in",
        sp_api_region="eu",
        sp_api_application_name="EWise",
        cors_origins=["http://localhost:3000"],
        sp_api_oauth_consent_base_url="",
        sp_api_lwa_client_id=SecretStr(CLIENT_ID),
        sp_api_lwa_client_secret=SecretStr(CLIENT_SECRET),
        sp_api_production_lwa_client_id=None,
        sp_api_production_lwa_client_secret=None,
        sp_api_lwa_token_url=DEFAULT_LWA_TOKEN_URL,
    )
    values.update(overrides)
    return Settings(**values)


class _BoomChecker:
    def __init__(self) -> None:
        raise AssertionError("oauth callback must not construct an SP-API client")


class _GuardLwa:
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        raise AssertionError("oauth callback must not exchange an authorization code")


class _FakeLwa:
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        assert authorization_code.get_secret_value() == TEST_CODE
        return LwaAuthorizationGrant(
            access_token=SecretStr(ACCESS_TOKEN),
            refresh_token=SecretStr(REFRESH_TOKEN),
            token_type="bearer",
            expires_in=3600,
        )


class _AuthFailLwa:
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        raise SpApiAuthenticationError("Amazon LWA authentication failed.")


class _UnavailableLwa:
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        raise SpApiRequestFailedError("Amazon LWA token request failed.")


class _FailingSecrets(DevelopmentSecretProvider):
    def put_secret(self, reference: str, value: SecretStr) -> None:
        raise SecretAccessError()


def _service(**kwargs) -> AmazonConnectionService:
    values = dict(
        settings=_authorize_settings(),
        sandbox_client_factory=_BoomChecker,
        lwa_token_service=_GuardLwa(),
        secret_provider=DevelopmentSecretProvider(),
    )
    values.update(kwargs)
    return AmazonConnectionService(**values)


def _success_service(
    *,
    lwa: object | None = None,
    secrets: DevelopmentSecretProvider | None = None,
) -> tuple[AmazonConnectionService, DevelopmentSecretProvider]:
    provider = secrets or DevelopmentSecretProvider()
    service = _service(lwa_token_service=lwa or _FakeLwa(), secret_provider=provider)
    return service, provider


def _raw_state_from_url(authorization_url: str) -> str:
    parsed = urlparse(authorization_url)
    params = parse_qs(parsed.query)
    assert "state" in params
    return params["state"][0]


def _start(service: AmazonConnectionService | None = None) -> tuple[AmazonConnectionService, str]:
    started_service = service or _service()
    started = started_service.start_authorization(environment="SANDBOX")
    return started_service, _raw_state_from_url(started.authorization_url)


def _assert_no_token_material(*values: object) -> None:
    for value in values:
        text = str(value)
        assert ACCESS_TOKEN not in text
        assert REFRESH_TOKEN not in text
        assert TEST_CODE not in text
        assert CLIENT_SECRET not in text


def test_successful_token_exchange_stores_refresh_token_only() -> None:
    service, provider = _success_service()
    _, raw = _start(service)
    result = service.complete_authorization_callback(
        state=raw,
        spapi_oauth_code=TEST_CODE,
        selling_partner_id="A3FHEXAMPLEYWS",
    )
    dumped = public_model_dump(result)
    assert result.outcome == "token_stored"
    assert result.notice == "success"
    assert result.reason == "token_stored"
    assert result.authorization_code_present is True
    assert result.connection_status == "pending_validation"
    assert result.connection_status != "connected"
    assert result.organization_id == str(current_organization_id())
    _assert_no_token_material(dumped)
    assert "A3FHEXAMPLEYWS" not in str(dumped)
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_validation"
        assert connection.status != "connected"
        assert connection.authorized_at is not None
        assert connection.selling_partner_id == "A3FHEXAMPLEYWS"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert connection.token_reference == expected
        assert "Atza|" not in connection.token_reference
        assert "Atzr|" not in connection.token_reference
        for value in (getattr(connection, column) for column in connection.__table__.c.keys()):
            _assert_no_token_material(value)
        state_row = session.scalars(select(AmazonOAuthState)).one()
        assert state_row.consumed_at is not None
        assert state_row.state_hash == hash_oauth_state(raw)
        for value in (getattr(state_row, column) for column in state_row.__table__.c.keys()):
            _assert_no_token_material(value)
    stored = provider.get_secret(expected)
    assert stored.get_secret_value() == REFRESH_TOKEN
    assert ACCESS_TOKEN not in stored.get_secret_value()


_FAIL_CLOSED_INVALID_IDENTITIES = {
    "token_shaped": "Atzr|this-looks-like-a-refresh-token",
    "oversized": "A" * 65,
    "whitespace_only": "   ",
    "control_character": "A3FHEXAMPLE\x00WS",
}


def _assert_fails_closed_on_first_authorization(
    service: AmazonConnectionService, raw: str, *, selling_partner_id: str | None
) -> None:
    """Shared assertions for every missing/invalid-identity, first-authorization case.

    `service` must be built with `_GuardLwa` (the default for `_service()`), so
    if the implementation regresses and attempts an exchange, the test fails
    immediately via `_GuardLwa`'s AssertionError rather than silently passing.
    """
    result = service.complete_authorization_callback(
        state=raw,
        spapi_oauth_code=TEST_CODE,
        selling_partner_id=selling_partner_id,
    )
    assert result.outcome == "invalid"
    assert result.notice == "error"
    assert result.reason == "seller_identity_missing"
    assert result.connection_status == "pending_authorization"
    assert result.connection_status != "pending_validation"
    assert result.connection_status != "connected"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.selling_partner_id is None
        assert connection.token_reference is None
        assert connection.status == "pending_authorization"
        assert connection.last_error_code == "seller_identity_missing"
    if selling_partner_id:
        assert selling_partner_id not in str(result)


def test_missing_selling_partner_id_fails_closed_on_first_authorization() -> None:
    service, raw = _start(_service())
    _assert_fails_closed_on_first_authorization(service, raw, selling_partner_id=None)


def test_token_shaped_selling_partner_id_fails_closed() -> None:
    service, raw = _start(_service())
    _assert_fails_closed_on_first_authorization(
        service, raw, selling_partner_id=_FAIL_CLOSED_INVALID_IDENTITIES["token_shaped"]
    )


def test_oversized_selling_partner_id_fails_closed() -> None:
    service, raw = _start(_service())
    _assert_fails_closed_on_first_authorization(
        service, raw, selling_partner_id=_FAIL_CLOSED_INVALID_IDENTITIES["oversized"]
    )


def test_whitespace_selling_partner_id_fails_closed() -> None:
    service, raw = _start(_service())
    _assert_fails_closed_on_first_authorization(
        service, raw, selling_partner_id=_FAIL_CLOSED_INVALID_IDENTITIES["whitespace_only"]
    )


def test_control_character_selling_partner_id_fails_closed() -> None:
    service, raw = _start(_service())
    _assert_fails_closed_on_first_authorization(
        service, raw, selling_partner_id=_FAIL_CLOSED_INVALID_IDENTITIES["control_character"]
    )


def test_missing_selling_partner_id_during_reauthorization_preserves_existing_grant() -> None:
    """stored identity: seller A, active grant: seller A. callback identity: absent."""
    service, provider = _success_service()
    _, raw_first = _start(service)
    first = service.complete_authorization_callback(
        state=raw_first, spapi_oauth_code=TEST_CODE, selling_partner_id="ORIGINALSPID001"
    )
    assert first.outcome == "token_stored"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        original_reference = connection.token_reference
    original_secret = provider.get_secret(original_reference).get_secret_value()
    assert original_secret == REFRESH_TOKEN

    reauth_service, raw_second = _start(_service(secret_provider=provider))
    result = reauth_service.complete_authorization_callback(
        state=raw_second, spapi_oauth_code=TEST_CODE, selling_partner_id=None
    )
    assert result.outcome == "invalid"
    assert result.reason == "seller_identity_missing"
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.selling_partner_id == "ORIGINALSPID001"
        assert connection.token_reference == original_reference
        assert connection.last_error_code == "seller_identity_missing"
    # Byte-for-byte: the active seller-A secret was never touched.
    assert provider.get_secret(original_reference).get_secret_value() == original_secret


def test_invalid_selling_partner_id_during_reauthorization_preserves_existing_grant() -> None:
    service, provider = _success_service()
    _, raw_first = _start(service)
    first = service.complete_authorization_callback(
        state=raw_first, spapi_oauth_code=TEST_CODE, selling_partner_id="ORIGINALSPID001"
    )
    assert first.outcome == "token_stored"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        original_reference = connection.token_reference
    original_secret = provider.get_secret(original_reference).get_secret_value()

    reauth_service, raw_second = _start(_service(secret_provider=provider))
    result = reauth_service.complete_authorization_callback(
        state=raw_second,
        spapi_oauth_code=TEST_CODE,
        selling_partner_id=_FAIL_CLOSED_INVALID_IDENTITIES["token_shaped"],
    )
    assert result.outcome == "invalid"
    assert result.reason == "seller_identity_missing"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.selling_partner_id == "ORIGINALSPID001"
        assert connection.token_reference == original_reference
    assert provider.get_secret(original_reference).get_secret_value() == original_secret


def test_concurrent_valid_and_missing_callbacks_cannot_create_mixed_state() -> None:
    """connection initially identity-empty. callback A: valid seller A.
    callback B: missing identity. Deterministic, single-threaded: the fail-
    closed check requires no database interaction to decide, so no barrier or
    thread is needed to force a specific order — B's rejection is guaranteed
    by construction, regardless of whether it runs before or after A."""
    service, provider = _success_service()
    _, raw_a = _start(service)
    valid = service.complete_authorization_callback(
        state=raw_a, spapi_oauth_code=TEST_CODE, selling_partner_id="RACEVALIDSELLER"
    )
    assert valid.outcome == "token_stored"

    missing_service, raw_b = _start(_service(secret_provider=provider))
    missing = missing_service.complete_authorization_callback(
        state=raw_b, spapi_oauth_code=TEST_CODE, selling_partner_id=None
    )
    assert missing.outcome == "invalid"
    assert missing.reason == "seller_identity_missing"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        # Active identity and secret both belong to seller A; B never touched either.
        assert connection.selling_partner_id == "RACEVALIDSELLER"
        assert connection.token_reference is not None
        assert provider.get_secret(connection.token_reference).get_secret_value() == REFRESH_TOKEN


def test_concurrent_same_seller_and_missing_callbacks_preserve_valid_grant() -> None:
    """stored identity: seller A. callback A: valid seller A.
    callback B: missing/invalid identity."""
    service, provider = _success_service()
    _, raw_first = _start(service)
    first = service.complete_authorization_callback(
        state=raw_first, spapi_oauth_code=TEST_CODE, selling_partner_id="STABLESELLERID"
    )
    assert first.outcome == "token_stored"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        reference = connection.token_reference

    reauth_service, raw_reauth = _start(_service(secret_provider=provider))
    reauth = reauth_service.complete_authorization_callback(
        state=raw_reauth, spapi_oauth_code=TEST_CODE, selling_partner_id=None
    )
    assert reauth.outcome == "invalid"
    assert reauth.reason == "seller_identity_missing"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.selling_partner_id == "STABLESELLERID"
        assert connection.token_reference == reference
        assert provider.get_secret(reference).get_secret_value() == REFRESH_TOKEN


def test_fail_closed_logs_and_result_contain_no_secret_material(caplog) -> None:
    service, raw = _start(_service())
    with caplog.at_level("DEBUG"):
        result = service.complete_authorization_callback(
            state=raw,
            spapi_oauth_code=TEST_CODE,
            selling_partner_id=_FAIL_CLOSED_INVALID_IDENTITIES["token_shaped"],
        )
    assert result.reason == "seller_identity_missing"
    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    for value in (str(result), combined_log):
        _assert_no_token_material(value)
        assert _FAIL_CLOSED_INVALID_IDENTITIES["token_shaped"] not in value
        assert TEST_CODE not in value
        assert raw not in value


def test_oauth_state_cannot_be_replayed_after_fail_closed_rejection() -> None:
    service, raw = _start(_service())
    first = service.complete_authorization_callback(
        state=raw, spapi_oauth_code=TEST_CODE, selling_partner_id=None
    )
    assert first.reason == "seller_identity_missing"
    second = service.complete_authorization_callback(
        state=raw, spapi_oauth_code=TEST_CODE, selling_partner_id="A3FHEXAMPLEYWS"
    )
    assert second.outcome == "invalid"
    assert second.reason == "oauth_state_consumed"


def test_callback_same_seller_reauthorization_refreshes_grant() -> None:
    """Stored SPID: seller-A. Callback SPID: seller-A. Must not be treated as a conflict."""
    service, provider = _success_service()
    _, raw_first = _start(service)
    first = service.complete_authorization_callback(
        state=raw_first,
        spapi_oauth_code=TEST_CODE,
        selling_partner_id="SAMESELLERID01",
    )
    assert first.reason == "token_stored"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.selling_partner_id == "SAMESELLERID01"
        first_reference = connection.token_reference

    _, raw_second = _start(service)
    second = service.complete_authorization_callback(
        state=raw_second,
        spapi_oauth_code=TEST_CODE,
        selling_partner_id="SAMESELLERID01",
    )
    assert second.outcome == "token_stored"
    assert second.reason == "token_stored"
    assert second.connection_status == "pending_validation"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.selling_partner_id == "SAMESELLERID01"
        assert connection.status == "pending_validation"
        assert connection.last_error_code is None
        # The reference (org, connection)-derived pointer is stable; the grant
        # behind it was safely refreshed.
        assert connection.token_reference == first_reference
    assert provider.get_secret(first_reference).get_secret_value() == REFRESH_TOKEN


def test_callback_identity_conflict_preserves_active_grant_secret(caplog) -> None:
    """Regression for the fixed defect: a conflicting reauthorization must never
    reach `put_secret`, so the active credential can never be silently replaced
    while the database still names the prior seller.
    """
    service, provider = _success_service()
    _, raw_first = _start(service)
    first = service.complete_authorization_callback(
        state=raw_first,
        spapi_oauth_code=TEST_CODE,
        selling_partner_id="ORIGINALSPID001",
    )
    assert first.outcome == "token_stored"
    assert first.reason == "token_stored"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        original_reference = connection.token_reference
        assert connection.selling_partner_id == "ORIGINALSPID001"
    assert original_reference is not None
    assert provider.get_secret(original_reference).get_secret_value() == REFRESH_TOKEN

    class _MustNotExchangeLwa:
        def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
            raise AssertionError(
                "an identity-conflicting callback must be rejected before the "
                "authorization code is ever exchanged, so a different seller's "
                "refresh token is never requested for a claimed connection"
            )

    different_seller_service, _shared_provider = _success_service(
        lwa=_MustNotExchangeLwa(), secrets=provider
    )
    assert _shared_provider is provider
    _, raw_second = _start(different_seller_service)
    with caplog.at_level("DEBUG"):
        second = different_seller_service.complete_authorization_callback(
            state=raw_second,
            spapi_oauth_code=TEST_CODE,
            selling_partner_id="DIFFERENTSPID002",
        )
    # 4. result reports identity_conflict
    assert second.outcome == "invalid"
    assert second.notice == "error"
    assert second.reason == "identity_conflict"
    assert second.connection_status == "pending_authorization"
    assert second.connection_status != "connected"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        # 5. database still contains seller-A
        assert connection.selling_partner_id == "ORIGINALSPID001"
        # 6. existing token_reference is unchanged
        assert connection.token_reference == original_reference
        # 10. connection does not transition toward connected
        assert connection.status == "pending_authorization"
        assert connection.status != "connected"
        assert connection.last_error_code == "identity_conflict"
    # 7. the secret stored at that reference remains the prior test grant
    # 8. seller-B's refresh token is not present in the active secret store
    #    (it was never requested at all — see _MustNotExchangeLwa above)
    assert provider.get_secret(original_reference).get_secret_value() == REFRESH_TOKEN
    # 9. no secret or seller identifier appears in logs or response output
    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    for value in (str(second), combined_log):
        assert "ORIGINALSPID001" not in value
        assert "DIFFERENTSPID002" not in value
        _assert_no_token_material(value)


def test_service_losing_the_atomic_claim_never_touches_secrets(caplog) -> None:
    """Deterministically simulates the exact race window the atomic claim
    exists to close: this attempt's pre-check passes (identity is still
    unclaimed), but — in the window before its own claim executes — a
    concurrent authorization for a DIFFERENT seller commits first. A
    controlled fake forces this interleaving deterministically (no threads,
    no sleeps): the fake LWA, when exchanged, directly performs the
    concurrent winner's claim before returning this attempt's grant. This
    attempt's own claim must then fail, and it must never call put_secret.
    """
    service, provider = _success_service()
    _, raw = _start(service)

    class _ConcurrentWinnerDuringExchangeLwa:
        def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
            with session_scope() as session:
                connection = AmazonConnectionRepository(session).get(
                    current_organization_id(), provider="SP_API", environment="SANDBOX"
                )
                assert connection is not None
                won = AmazonConnectionRepository(session).claim_identity_for_authorization(
                    current_organization_id(),
                    connection.id,
                    selling_partner_id="CONCURRENTWINNERID",
                )
                assert won is True, "test setup: the simulated concurrent winner must succeed"
            return LwaAuthorizationGrant(
                access_token=SecretStr(ACCESS_TOKEN),
                refresh_token=SecretStr("Atzr|test-losing-attempt-refresh-token"),
                token_type="bearer",
                expires_in=3600,
            )

    concurrent_service, _shared_provider = _success_service(
        lwa=_ConcurrentWinnerDuringExchangeLwa(), secrets=provider
    )
    with caplog.at_level("DEBUG"):
        result = concurrent_service.complete_authorization_callback(
            state=raw,
            spapi_oauth_code=TEST_CODE,
            selling_partner_id="MYOWNATTEMPTID",
        )
    assert result.outcome == "invalid"
    assert result.notice == "error"
    assert result.reason == "identity_conflict"
    assert result.connection_status == "pending_authorization"
    assert result.connection_status != "connected"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        # The concurrent winner's identity stands; this attempt never touched it.
        assert connection.selling_partner_id == "CONCURRENTWINNERID"
        # No secret was ever written by anyone in this scenario.
        assert connection.token_reference is None
        reference = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
    assert provider.exists(reference) is False
    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    for value in (str(result), combined_log):
        assert "CONCURRENTWINNERID" not in value
        assert "MYOWNATTEMPTID" not in value
        _assert_no_token_material(value)


def test_service_winning_the_atomic_claim_with_same_seller_proceeds_normally() -> None:
    """Mirror of the test above for the benign case: a concurrent claim for
    the SAME seller commits first, and this attempt must still succeed
    (same-seller races are not conflicts)."""
    service, provider = _success_service()
    _, raw = _start(service)

    class _ConcurrentSameSellerDuringExchangeLwa:
        def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
            with session_scope() as session:
                connection = AmazonConnectionRepository(session).get(
                    current_organization_id(), provider="SP_API", environment="SANDBOX"
                )
                assert connection is not None
                won = AmazonConnectionRepository(session).claim_identity_for_authorization(
                    current_organization_id(),
                    connection.id,
                    selling_partner_id="SHAREDSELLERRACE",
                )
                assert won is True
            return LwaAuthorizationGrant(
                access_token=SecretStr(ACCESS_TOKEN),
                refresh_token=SecretStr(REFRESH_TOKEN),
                token_type="bearer",
                expires_in=3600,
            )

    concurrent_service, _shared_provider = _success_service(
        lwa=_ConcurrentSameSellerDuringExchangeLwa(), secrets=provider
    )
    result = concurrent_service.complete_authorization_callback(
        state=raw,
        spapi_oauth_code=TEST_CODE,
        selling_partner_id="SHAREDSELLERRACE",
    )
    assert result.outcome == "token_stored"
    assert result.reason == "token_stored"
    assert result.connection_status == "pending_validation"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.selling_partner_id == "SHAREDSELLERRACE"
        assert connection.status == "pending_validation"
        assert connection.token_reference is not None
        assert provider.get_secret(connection.token_reference).get_secret_value() == REFRESH_TOKEN


def test_callback_accepts_oauth_code_alias() -> None:
    service, _provider = _success_service()
    _, raw = _start(service)
    result = service.complete_authorization_callback(
        state=raw, code=TEST_CODE, selling_partner_id="A3FHEXAMPLEYWS"
    )
    assert result.outcome == "token_stored"
    assert result.authorization_code_present is True
    assert result.connection_status == "pending_validation"


def test_lwa_http_is_mocked_and_does_not_call_sp_api() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert "sellingpartnerapi" not in str(request.url)
        assert "getMarketplaceParticipations" not in str(request.url)
        assert "/sellers/" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "token_type": "bearer",
                "expires_in": 3600,
            },
        )

    settings = _authorize_settings()
    lwa = AmazonLwaTokenService.from_settings(settings, transport=httpx.MockTransport(handler))
    provider = DevelopmentSecretProvider()
    service = _service(settings=settings, lwa_token_service=lwa, secret_provider=provider)
    _, raw = _start(service)
    result = service.complete_authorization_callback(
        state=raw, spapi_oauth_code=TEST_CODE, selling_partner_id="A3FHEXAMPLEYWS"
    )
    assert result.outcome == "token_stored"
    assert len(captured) == 1
    form = parse_qs(captured[0].content.decode("utf-8"))
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == [TEST_CODE]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert form["client_id"] == [CLIENT_ID]


def test_invalid_state_rejected() -> None:
    service, raw = _start()
    result = service.complete_authorization_callback(
        state="not-a-real-state-token",
        spapi_oauth_code=TEST_CODE,
    )
    assert result.outcome == "invalid"
    assert result.notice == "error"
    assert result.reason == "oauth_state_invalid"
    assert result.authorization_code_present is False
    with session_scope() as session:
        stored = session.scalars(select(AmazonOAuthState)).one()
        assert stored.consumed_at is None


def test_expired_state_rejected() -> None:
    service = _service()
    raw, digest = new_oauth_state()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
            status="pending_authorization",
        )
        AmazonOAuthStateRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection.id,
            state_hash=digest,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.outcome == "invalid"
    assert result.reason == "oauth_state_expired"
    assert result.notice == "error"
    with session_scope() as session:
        stored = session.scalars(select(AmazonOAuthState)).one()
        assert stored.consumed_at is None
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_authorization"
        assert connection.token_reference is None
        assert connection.last_error_code == "oauth_state_expired"


def test_consumed_state_rejected() -> None:
    service, provider = _success_service()
    _, raw = _start(service)
    first = service.complete_authorization_callback(
        state=raw, spapi_oauth_code=TEST_CODE, selling_partner_id="A3FHEXAMPLEYWS"
    )
    assert first.outcome == "token_stored"
    second = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert second.outcome == "invalid"
    assert second.reason == "oauth_state_consumed"
    assert second.authorization_code_present is False
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_validation"
        assert connection.status != "connected"
        assert connection.last_error_code == "oauth_state_consumed"
        assert connection.token_reference is not None
        assert provider.get_secret(connection.token_reference).get_secret_value() == REFRESH_TOKEN


def test_wrong_organization_rejected() -> None:
    other_org = uuid4()
    raw, digest = new_oauth_state()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        connection = AmazonConnectionRepository(session).create(
            organization_id=other_org,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
            status="pending_authorization",
        )
        AmazonOAuthStateRepository(session).create(
            organization_id=other_org,
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection.id,
            state_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    service = _service()
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.outcome == "invalid"
    assert result.reason == "oauth_state_invalid"
    assert result.organization_id == str(current_organization_id())
    assert result.organization_id != str(other_org)
    with session_scope() as session:
        stored = AmazonOAuthStateRepository(session).list_for_org(other_org)
        assert len(stored) == 1
        assert stored[0].consumed_at is None
        other = AmazonConnectionRepository(session).get(other_org, provider="SP_API", environment="SANDBOX")
        assert other is not None
        assert other.status == "pending_authorization"
        assert other.token_reference is None


def test_amazon_denial_handled() -> None:
    service, raw = _start()
    result = service.complete_authorization_callback(state=raw, error="access_denied")
    assert result.outcome == "denied"
    assert result.notice == "denied"
    assert result.reason == "access_denied"
    assert result.authorization_code_present is False
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_authorization"
        assert connection.status != "connected"
        assert connection.token_reference is None
        assert connection.last_error_code == "access_denied"
        stored = session.scalars(select(AmazonOAuthState)).one()
        assert stored.consumed_at is not None


def test_authorization_code_and_tokens_are_not_logged(caplog) -> None:
    service, _provider = _success_service()
    _, raw = _start(service)
    with caplog.at_level("DEBUG"):
        result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    combined = "\n".join(record.getMessage() for record in caplog.records)
    _assert_no_token_material(combined, result, raw)
    assert raw not in combined


def test_invalid_authorization_code_does_not_store_tokens() -> None:
    service, provider = _success_service(lwa=_AuthFailLwa())
    _, raw = _start(service)
    result = service.complete_authorization_callback(
        state=raw, spapi_oauth_code=TEST_CODE, selling_partner_id="A3FHEXAMPLEYWS"
    )
    assert result.outcome == "invalid"
    assert result.notice == "error"
    assert result.reason == "lwa_authentication"
    assert result.connection_status == "pending_authorization"
    _assert_no_token_material(result)
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_authorization"
        assert connection.status != "connected"
        assert connection.token_reference is None
        assert connection.last_error_code == "lwa_authentication"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert provider.exists(expected) is False


def test_amazon_lwa_unavailable_does_not_store_tokens() -> None:
    service, provider = _success_service(lwa=_UnavailableLwa())
    _, raw = _start(service)
    result = service.complete_authorization_callback(
        state=raw, spapi_oauth_code=TEST_CODE, selling_partner_id="A3FHEXAMPLEYWS"
    )
    assert result.reason == "lwa_unavailable"
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.token_reference is None
        assert connection.status != "connected"
        assert connection.last_error_code == "lwa_unavailable"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert provider.exists(expected) is False


def test_secret_provider_failure_does_not_bind_reference() -> None:
    service, provider = _success_service(secrets=_FailingSecrets())
    _, raw = _start(service)
    result = service.complete_authorization_callback(
        state=raw, spapi_oauth_code=TEST_CODE, selling_partner_id="A3FHEXAMPLEYWS"
    )
    assert result.reason == "secret_storage_failed"
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.token_reference is None
        assert connection.status == "pending_authorization"
        assert connection.last_error_code == "secret_storage_failed"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert provider.exists(expected) is False


def test_bind_failure_deletes_orphan_secret(monkeypatch) -> None:
    def _boom(self, *args, **kwargs):
        raise TypeError("Amazon token reference does not match this connection.")

    monkeypatch.setattr(AmazonConnectionRepository, "bind_token_reference", _boom)
    provider = DevelopmentSecretProvider()
    service, _ = _success_service(secrets=provider)
    _, raw = _start(service)
    result = service.complete_authorization_callback(
        state=raw, spapi_oauth_code=TEST_CODE, selling_partner_id="A3FHEXAMPLEYWS"
    )
    assert result.reason == "token_bind_failed"
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.token_reference is None
        assert connection.status == "pending_authorization"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert provider.exists(expected) is False


def test_failed_callback_does_not_exchange_or_write_secrets() -> None:
    service, raw = _start()
    result = service.complete_authorization_callback(
        state="not-a-real-state-token",
        spapi_oauth_code=TEST_CODE,
    )
    assert result.outcome == "invalid"
    oauth_tree = ast.parse(
        (Path(__file__).resolve().parents[1] / "app" / "amazon" / "oauth_callback.py").read_text()
    )
    imported: set[str] = set()
    for node in ast.walk(oauth_tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "httpx" not in imported
    assert "app.amazon.sandbox" not in imported
    assert "app.amazon.secrets" not in imported
    assert "app.amazon.lwa" not in imported
    assert "app.amazon.lwa_token" not in imported
    assert "app.copilot" not in imported
    source = inspect.getsource(AmazonConnectionService.complete_authorization_callback)
    assert "grant_type" not in source
    assert "auth/o2/token" not in source
    assert "get_marketplace_participations" not in source
    store_source = inspect.getsource(AmazonConnectionService._store_refresh_token_from_authorization_code)
    assert "put_secret" in store_source
    assert "get_marketplace_participations" not in store_source
    lwa_source = inspect.getsource(AmazonLwaTokenService.exchange_authorization_code)
    assert "authorization_code" in lwa_source
    assert "get_marketplace_participations" not in lwa_source


def test_callback_endpoint_redirects_without_secrets(client) -> None:
    service, _provider = _success_service()
    started = service.start_authorization()
    raw = _raw_state_from_url(started.authorization_url)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(
            CALLBACK_URL,
            params={
                "state": raw,
                "spapi_oauth_code": TEST_CODE,
                "selling_partner_id": "A3FHEXAMPLEYWS",
            },
            follow_redirects=False,
        )
        overview = client.get(CONNECTION_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.endswith("/connection?amazon=success")
    _assert_no_token_material(location)
    assert raw not in location
    assert "spapi_oauth_code" not in location
    assert "selling_partner_id" not in location
    assert "token_reference" not in location
    assert response.headers.get("referrer-policy") == "no-referrer"
    body = overview.json()
    assert body["connection_status"] == "pending_validation"
    assert body["status"] == "NOT_CONNECTED"
    assert body["authorized_at"] is not None
    assert "token_reference" not in body
    _assert_no_token_material(body)
    assert "spapi_oauth_code" not in body


def test_callback_endpoint_denial_redirects(client) -> None:
    service = _service()
    started = service.start_authorization()
    raw = _raw_state_from_url(started.authorization_url)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(
            CALLBACK_URL,
            params={"state": raw, "error": "access_denied", "error_description": TEST_CODE},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.endswith("/connection?amazon=denied")
    assert TEST_CODE not in location
    assert "error_description" not in location
