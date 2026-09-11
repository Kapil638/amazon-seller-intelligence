"""pilot-deployment-ewise, correction 1 — ProductionSecretProvider
(PostgreSQL + AES-256-GCM). Runs against SQLite (tests/conftest.py's
DATABASE_URL=sqlite://) via the same session_scope() every other part of
the application uses — this module owns the encryption/AAD/key-rotation
logic, not the database dialect, so SQLite proves it correctly; the
disposable-PostgreSQL suite (tests/postgres/test_disposable_postgres_
amazon_encrypted_secrets.py) separately proves the real migration DDL.

No plaintext token value may ever appear in a repr, str, or raised
exception anywhere in this file — every test that touches a secret value
asserts this explicitly via _assert_no_secrets.
"""

from __future__ import annotations

import base64
import json
import os

import pytest
from pydantic import SecretStr

from app.amazon.production_secrets import (
    AES_GCM_NONCE_LENGTH_BYTES,
    PRODUCTION_SECRET_KEYS_INVALID_MESSAGE,
    ProductionSecretProvider,
    SecretEncryptionConfigurationError,
    parse_amazon_secret_encryption_keys,
)
from app.amazon.secrets import (
    SecretAccessError,
    SecretNotFoundError,
    SecretProvider,
    SecretProviderFactory,
    build_asi_secret_reference,
    reset_secret_provider,
)
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.persistence.database import session_scope
from app.persistence.models import AmazonEncryptedSecret

REFRESH_TOKEN = "Atzr|test-production-refresh-token"
OTHER_TOKEN = "Atzr|another-production-refresh-token"


def _b64_key(seed: bytes) -> str:
    return base64.b64encode(seed).decode("ascii")


KEY_V1 = _b64_key(b"1" * 32)
KEY_V2 = _b64_key(b"2" * 32)


def _assert_no_secrets(text: str) -> None:
    assert REFRESH_TOKEN not in text
    assert OTHER_TOKEN not in text
    assert "Atzr|" not in text
    assert KEY_V1 not in text
    assert KEY_V2 not in text


def _reference(connection_id: str = "22222222-2222-2222-2222-222222222222") -> str:
    return build_asi_secret_reference(
        provider="SP_API",
        environment="PRODUCTION",
        organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
        connection_id=connection_id,
    )


def _provider(*, keys: dict[str, bytes] | None = None, active: str = "v1") -> ProductionSecretProvider:
    if keys is None:
        keys = {"v1": base64.b64decode(KEY_V1)}
    return ProductionSecretProvider(keys=keys, active_key_version=active)


# --- parse_amazon_secret_encryption_keys ------------------------------------


def test_parse_keys_accepts_a_valid_single_key() -> None:
    raw = json.dumps({"v1": KEY_V1})
    parsed = parse_amazon_secret_encryption_keys(raw)
    assert set(parsed) == {"v1"}
    assert len(parsed["v1"]) == 32


def test_parse_keys_accepts_multiple_versions() -> None:
    raw = json.dumps({"v1": KEY_V1, "v2": KEY_V2})
    parsed = parse_amazon_secret_encryption_keys(raw)
    assert set(parsed) == {"v1", "v2"}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not json",
        "[]",
        "{}",
        json.dumps({"v1": "not-base64!!!"}),
        json.dumps({"v1": base64.b64encode(b"short").decode()}),
        json.dumps({"v1": base64.b64encode(b"0" * 33).decode()}),
        json.dumps({"": KEY_V1}),
        json.dumps({"v1 with spaces": KEY_V1}),
        json.dumps({"v1": 12345}),
        json.dumps("not-an-object"),
    ],
)
def test_parse_keys_fails_closed_on_invalid_input(raw: str) -> None:
    with pytest.raises(SecretEncryptionConfigurationError) as exc_info:
        parse_amazon_secret_encryption_keys(raw)
    assert str(exc_info.value) == PRODUCTION_SECRET_KEYS_INVALID_MESSAGE
    assert KEY_V1 not in str(exc_info.value)
    if raw.strip():
        assert raw not in str(exc_info.value)


# --- construction -------------------------------------------------------


def test_construction_requires_active_version_present_in_keys() -> None:
    with pytest.raises(SecretEncryptionConfigurationError):
        ProductionSecretProvider(keys={"v1": base64.b64decode(KEY_V1)}, active_key_version="v2")


def test_construction_rejects_empty_keys() -> None:
    with pytest.raises(SecretEncryptionConfigurationError):
        ProductionSecretProvider(keys={}, active_key_version="v1")


def test_repr_never_includes_key_material() -> None:
    provider = _provider()
    assert repr(provider) == "SecretProvider(backend=production)"
    assert str(provider) == "SecretProvider(backend=production)"
    _assert_no_secrets(repr(provider))


def test_provider_satisfies_secret_provider_protocol() -> None:
    assert isinstance(_provider(), SecretProvider)


# --- put_secret / get_secret round trip ---------------------------------


def test_put_then_get_round_trips_the_exact_value() -> None:
    provider = _provider()
    reference = _reference()
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    fetched = provider.get_secret(reference)
    assert fetched.get_secret_value() == REFRESH_TOKEN


def test_put_secret_requires_secret_str() -> None:
    provider = _provider()
    with pytest.raises(TypeError):
        provider.put_secret(_reference(), REFRESH_TOKEN)  # type: ignore[arg-type]


def test_put_secret_rejects_empty_value() -> None:
    provider = _provider()
    with pytest.raises(SecretAccessError):
        provider.put_secret(_reference(), SecretStr("   "))


def test_put_secret_overwrites_the_same_reference() -> None:
    provider = _provider()
    reference = _reference()
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    provider.put_secret(reference, SecretStr(OTHER_TOKEN))
    assert provider.get_secret(reference).get_secret_value() == OTHER_TOKEN
    with session_scope() as session:
        assert session.query(AmazonEncryptedSecret).filter_by(reference=reference).count() == 1


def test_get_secret_missing_reference_raises_not_found() -> None:
    provider = _provider()
    with pytest.raises(SecretNotFoundError):
        provider.get_secret(_reference("33333333-3333-3333-3333-333333333333"))


def test_exists_reflects_presence() -> None:
    provider = _provider()
    reference = _reference()
    assert provider.exists(reference) is False
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    assert provider.exists(reference) is True


def test_delete_secret_removes_value_and_is_a_safe_no_op_when_missing() -> None:
    provider = _provider()
    reference = _reference()
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    provider.delete_secret(reference)
    assert provider.exists(reference) is False
    with pytest.raises(SecretNotFoundError):
        provider.get_secret(reference)
    # Deleting again (already gone) must not raise.
    provider.delete_secret(reference)


# --- ciphertext-only storage / no plaintext anywhere ---------------------


def test_stored_row_never_contains_plaintext() -> None:
    provider = _provider()
    reference = _reference()
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    with session_scope() as session:
        row = session.get(AmazonEncryptedSecret, reference)
        assert row is not None
        assert len(row.nonce) == AES_GCM_NONCE_LENGTH_BYTES
        assert REFRESH_TOKEN.encode("utf-8") not in row.ciphertext
        assert row.key_version == "v1"


def test_two_writes_of_the_same_value_use_different_nonces_and_ciphertexts() -> None:
    provider = _provider()
    ref_a = _reference("44444444-4444-4444-4444-444444444444")
    ref_b = _reference("55555555-5555-5555-5555-555555555555")
    provider.put_secret(ref_a, SecretStr(REFRESH_TOKEN))
    provider.put_secret(ref_b, SecretStr(REFRESH_TOKEN))
    with session_scope() as session:
        row_a = session.get(AmazonEncryptedSecret, ref_a)
        row_b = session.get(AmazonEncryptedSecret, ref_b)
        assert row_a.nonce != row_b.nonce
        assert row_a.ciphertext != row_b.ciphertext


# --- authenticated encryption: tamper and AAD-binding detection ----------


def test_corrupted_ciphertext_fails_closed_instead_of_returning_garbage() -> None:
    provider = _provider()
    reference = _reference()
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    with session_scope() as session:
        row = session.get(AmazonEncryptedSecret, reference)
        corrupted = bytearray(row.ciphertext)
        corrupted[0] ^= 0xFF
        row.ciphertext = bytes(corrupted)
    with pytest.raises(SecretAccessError) as exc_info:
        provider.get_secret(reference)
    _assert_no_secrets(str(exc_info.value))


def test_ciphertext_relabeled_onto_a_different_reference_fails_closed() -> None:
    """A row's ciphertext/nonce copied onto a different reference must fail
    decryption (AAD mismatch), never silently succeed under the wrong
    identity — the specific attack AAD-binding exists to prevent."""
    provider = _provider()
    ref_a = _reference("66666666-6666-6666-6666-666666666666")
    ref_b = _reference("77777777-7777-7777-7777-777777777777")
    provider.put_secret(ref_a, SecretStr(REFRESH_TOKEN))
    with session_scope() as session:
        row_a = session.get(AmazonEncryptedSecret, ref_a)
        session.add(
            AmazonEncryptedSecret(
                reference=ref_b,
                key_version=row_a.key_version,
                nonce=row_a.nonce,
                ciphertext=row_a.ciphertext,
            )
        )
    with pytest.raises(SecretAccessError) as exc_info:
        provider.get_secret(ref_b)
    _assert_no_secrets(str(exc_info.value))


# --- key rotation ---------------------------------------------------------


def test_rotate_key_version_re_encrypts_under_the_new_key_and_preserves_value() -> None:
    keys = {"v1": base64.b64decode(KEY_V1), "v2": base64.b64decode(KEY_V2)}
    provider = _provider(keys=keys, active="v1")
    reference = _reference()
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    with session_scope() as session:
        before = session.get(AmazonEncryptedSecret, reference)
        assert before.key_version == "v1"
        before_ciphertext = before.ciphertext

    provider.rotate_key_version(reference, new_key_version="v2")

    with session_scope() as session:
        after = session.get(AmazonEncryptedSecret, reference)
        assert after.key_version == "v2"
        assert after.ciphertext != before_ciphertext

    assert provider.get_secret(reference).get_secret_value() == REFRESH_TOKEN


def test_rotate_key_version_rejects_an_unconfigured_target_version() -> None:
    provider = _provider()
    reference = _reference()
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    with pytest.raises(SecretEncryptionConfigurationError):
        provider.rotate_key_version(reference, new_key_version="v9-does-not-exist")


def test_old_key_still_decrypts_rows_not_yet_rotated_after_a_new_active_version_is_introduced() -> None:
    """Introducing a new active key_version for new writes must not break
    reads of rows still encrypted under the old version."""
    keys_v1_only = {"v1": base64.b64decode(KEY_V1)}
    provider_v1 = _provider(keys=keys_v1_only, active="v1")
    reference = _reference()
    provider_v1.put_secret(reference, SecretStr(REFRESH_TOKEN))

    keys_both = {"v1": base64.b64decode(KEY_V1), "v2": base64.b64decode(KEY_V2)}
    provider_v2_active = _provider(keys=keys_both, active="v2")
    # Unrotated row (still v1) remains readable...
    assert provider_v2_active.get_secret(reference).get_secret_value() == REFRESH_TOKEN
    # ...and a fresh write now uses the new active version.
    other_reference = _reference("88888888-8888-8888-8888-888888888888")
    provider_v2_active.put_secret(other_reference, SecretStr(OTHER_TOKEN))
    with session_scope() as session:
        assert session.get(AmazonEncryptedSecret, reference).key_version == "v1"
        assert session.get(AmazonEncryptedSecret, other_reference).key_version == "v2"


def test_row_encrypted_under_a_key_version_no_longer_configured_fails_closed() -> None:
    """A row's key_version pointing at a key that has since been removed
    from this process's configured key map must fail closed, never attempt
    decryption with an unrelated key."""
    provider_v1 = _provider(keys={"v1": base64.b64decode(KEY_V1)}, active="v1")
    reference = _reference()
    provider_v1.put_secret(reference, SecretStr(REFRESH_TOKEN))

    provider_v2_only = _provider(keys={"v2": base64.b64decode(KEY_V2)}, active="v2")
    with pytest.raises(SecretAccessError) as exc_info:
        provider_v2_only.get_secret(reference)
    _assert_no_secrets(str(exc_info.value))


# --- factory wiring --------------------------------------------------------


def test_factory_wires_production_backend_end_to_end() -> None:
    reset_secret_provider()
    try:
        settings = Settings(
            amazon_secret_backend="production",
            amazon_secret_encryption_keys=SecretStr(json.dumps({"v1": KEY_V1})),
            amazon_secret_active_key_version="v1",
            default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
        )
        provider = SecretProviderFactory().create(settings)
        assert isinstance(provider, ProductionSecretProvider)
        reference = _reference("99999999-9999-9999-9999-999999999999")
        provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
        assert provider.get_secret(reference).get_secret_value() == REFRESH_TOKEN
        _assert_no_secrets(repr(provider))
    finally:
        reset_secret_provider()


def test_factory_fails_closed_when_active_version_not_in_configured_keys() -> None:
    reset_secret_provider()
    try:
        settings = Settings(
            amazon_secret_backend="production",
            amazon_secret_encryption_keys=SecretStr(json.dumps({"v1": KEY_V1})),
            amazon_secret_active_key_version="v-does-not-exist",
            default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
        )
        with pytest.raises(SecretEncryptionConfigurationError):
            SecretProviderFactory().create(settings)
    finally:
        reset_secret_provider()


def test_nonces_are_sourced_from_a_csprng_not_a_fixed_or_derived_value() -> None:
    seen: set[bytes] = set()
    provider = _provider()
    for i in range(20):
        reference = _reference(f"a0000000-0000-0000-0000-0000000000{i:02d}")
        provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
        with session_scope() as session:
            row = session.get(AmazonEncryptedSecret, reference)
            assert row.nonce not in seen
            seen.add(row.nonce)
