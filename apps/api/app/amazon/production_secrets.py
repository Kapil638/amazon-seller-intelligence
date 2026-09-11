"""Production `SecretProvider` backend: PostgreSQL + AES-256-GCM.

pilot-deployment-ewise, correction 1. See `app.amazon.secrets` for the
`SecretProvider` contract this must satisfy without changing any call
site, and `app.persistence.models.AmazonEncryptedSecret` for the storage
design. Imported lazily from `SecretProviderFactory.create()` (never at
`app.amazon.secrets` module scope) to avoid a circular import, since this
module itself imports from `app.amazon.secrets`.

Threat model / boundaries:
- Ciphertext, nonce, and key_version are the only things this module ever
  writes to the database. Plaintext token material exists only inside
  this process's memory, only for the duration of one encrypt/decrypt
  call, and is never logged, `repr()`'d, or included in an exception.
- AES-256-GCM (via `cryptography`'s `AESGCM`) is authenticated encryption:
  a tampered ciphertext, nonce, or associated-data mismatch raises
  `InvalidTag`, translated here into `SecretAccessError` — never a silent
  garbage plaintext.
- The ASI secret reference string (already validated, non-secret, stable
  — see `validate_secret_reference`) is passed as AES-GCM associated data
  on every encrypt/decrypt, cryptographically binding a ciphertext to the
  exact reference it was stored under. Copying one row's ciphertext/nonce
  onto another row's reference fails decryption rather than silently
  succeeding under the wrong identity.
- A fresh, CSPRNG-sourced (`os.urandom`) 96-bit nonce is generated for
  every single write. Never reused, never derived from the reference or
  a counter.
- Master key material never enters the database and never enters this
  process except via `Settings.amazon_secret_encryption_keys` (a Railway
  encrypted environment variable in any deployed environment).
- Fail closed: missing, empty, malformed, or wrong-length key
  configuration raises at construction time (i.e. at
  `SecretProviderFactory.create()`, before any secret operation is
  attempted) — never lazily on first `get_secret`/`put_secret`, and never
  falls back to another backend.
- Does not manage database connections or transactions itself — every
  operation runs through `app.persistence.database.session_scope()`, the
  same transactional boundary (and the same production-database-access
  guard, `_guard_engine_creation`) every other part of this application
  already uses.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app.amazon.secrets import (
    AMAZON_SECRET_BACKEND_PRODUCTION,
    SECRET_ACCESS_FAILURE_MESSAGE,
    SECRET_NOT_FOUND_MESSAGE,
    SecretAccessError,
    SecretNotFoundError,
    secret_provider_repr,
    validate_secret_reference,
)
from app.persistence.database import session_scope
from app.persistence.models import AmazonEncryptedSecret

AES_KEY_LENGTH_BYTES = 32
AES_GCM_NONCE_LENGTH_BYTES = 12
_KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

PRODUCTION_SECRET_KEYS_INVALID_MESSAGE = (
    "Amazon production secret backend encryption keys are missing or invalid."
)


class SecretEncryptionConfigurationError(SecretAccessError):
    """Raised when the production backend's key material is missing,
    malformed, or does not include the configured active key_version.
    Never includes any raw input, key_version, or decoded byte — see
    `parse_amazon_secret_encryption_keys`'s own docstring."""

    def __init__(self, message: str = PRODUCTION_SECRET_KEYS_INVALID_MESSAGE) -> None:
        super().__init__(message)


def parse_amazon_secret_encryption_keys(raw: str) -> dict[str, bytes]:
    """Parse `AMAZON_SECRET_ENCRYPTION_KEYS`: a JSON object of
    `{key_version: base64(32 raw bytes)}`.

    Fails closed (raises `SecretEncryptionConfigurationError`) on: empty
    input, invalid JSON, a non-object payload, an empty object, any
    key_version failing `_KEY_VERSION_PATTERN`, any value that is not a
    string, any value that is not valid base64, or any decoded key whose
    length is not exactly 32 bytes (AES-256). The raised exception never
    includes the raw input, a key_version, or any decoded byte — only the
    fixed, generic message.
    """
    text = (raw or "").strip()
    if not text:
        raise SecretEncryptionConfigurationError()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SecretEncryptionConfigurationError() from exc
    if not isinstance(payload, dict) or not payload:
        raise SecretEncryptionConfigurationError()
    keys: dict[str, bytes] = {}
    for version, encoded in payload.items():
        if not isinstance(version, str) or not _KEY_VERSION_PATTERN.match(version):
            raise SecretEncryptionConfigurationError()
        if not isinstance(encoded, str):
            raise SecretEncryptionConfigurationError()
        try:
            key_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SecretEncryptionConfigurationError() from exc
        if len(key_bytes) != AES_KEY_LENGTH_BYTES:
            raise SecretEncryptionConfigurationError()
        keys[version] = key_bytes
    return keys


class ProductionSecretProvider:
    """PostgreSQL + AES-256-GCM `SecretProvider` backend. See module
    docstring for the full threat model."""

    def __init__(self, *, keys: dict[str, bytes], active_key_version: str) -> None:
        if not keys or active_key_version not in keys:
            raise SecretEncryptionConfigurationError()
        self._keys = dict(keys)
        self._active_key_version = active_key_version

    def __repr__(self) -> str:
        return secret_provider_repr(backend=AMAZON_SECRET_BACKEND_PRODUCTION)

    def __str__(self) -> str:
        return self.__repr__()

    def put_secret(self, reference: str, value: SecretStr) -> None:
        if not isinstance(value, SecretStr):
            raise TypeError("put_secret requires SecretStr")
        plaintext = value.get_secret_value().strip()
        if not plaintext:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE)
        key = validate_secret_reference(reference)
        self._write(key, self._active_key_version, plaintext)

    def get_secret(self, reference: str) -> SecretStr:
        key = validate_secret_reference(reference)
        key_version, nonce, ciphertext = self._read_row(key)
        material = self._keys.get(key_version)
        if material is None:
            # A row encrypted under a key_version no longer present in
            # this process's configured key map (e.g. removed before its
            # rows were rotated forward) — fail closed, never attempt a
            # decrypt with the wrong key.
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE)
        return SecretStr(self._decrypt(material, key, nonce, ciphertext))

    def exists(self, reference: str) -> bool:
        key = validate_secret_reference(reference)
        try:
            with session_scope() as session:
                return session.get(AmazonEncryptedSecret, key) is not None
        except SecretAccessError:
            raise
        except Exception as exc:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE) from exc

    def delete_secret(self, reference: str) -> None:
        key = validate_secret_reference(reference)
        try:
            with session_scope() as session:
                row = session.get(AmazonEncryptedSecret, key)
                if row is not None:
                    session.delete(row)
        except SecretAccessError:
            raise
        except Exception as exc:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE) from exc

    def rotate_key_version(self, reference: str, *, new_key_version: str) -> None:
        """Re-encrypt one row under a different configured key_version
        (typically the new active version after a rotation) with a fresh
        nonce, without ever exposing plaintext outside this method's own
        local scope. `reference` (the AAD) is unchanged, so the row
        remains bound to the same identity after rotation."""
        if new_key_version not in self._keys:
            raise SecretEncryptionConfigurationError()
        key = validate_secret_reference(reference)
        old_key_version, nonce, ciphertext = self._read_row(key)
        material = self._keys.get(old_key_version)
        if material is None:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE)
        plaintext = self._decrypt(material, key, nonce, ciphertext)
        self._write(key, new_key_version, plaintext)

    def _read_row(self, key: str) -> tuple[str, bytes, bytes]:
        try:
            with session_scope() as session:
                row = session.get(AmazonEncryptedSecret, key)
                if row is None:
                    raise SecretNotFoundError(SECRET_NOT_FOUND_MESSAGE)
                return row.key_version, row.nonce, row.ciphertext
        except (SecretNotFoundError, SecretAccessError):
            raise
        except Exception as exc:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE) from exc

    def _decrypt(self, material: bytes, key: str, nonce: bytes, ciphertext: bytes) -> str:
        aesgcm = AESGCM(material)
        try:
            plaintext = aesgcm.decrypt(bytes(nonce), bytes(ciphertext), key.encode("utf-8"))
        except InvalidTag as exc:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE) from exc
        return plaintext.decode("utf-8")

    def _write(self, key: str, key_version: str, plaintext: str) -> None:
        material = self._keys[key_version]
        nonce = os.urandom(AES_GCM_NONCE_LENGTH_BYTES)
        aesgcm = AESGCM(material)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), key.encode("utf-8"))
        try:
            with session_scope() as session:
                existing = session.get(AmazonEncryptedSecret, key)
                if existing is None:
                    session.add(
                        AmazonEncryptedSecret(
                            reference=key, key_version=key_version, nonce=nonce, ciphertext=ciphertext
                        )
                    )
                else:
                    existing.key_version = key_version
                    existing.nonce = nonce
                    existing.ciphertext = ciphertext
                session.flush()
        except SecretAccessError:
            raise
        except Exception as exc:
            raise SecretAccessError(SECRET_ACCESS_FAILURE_MESSAGE) from exc
