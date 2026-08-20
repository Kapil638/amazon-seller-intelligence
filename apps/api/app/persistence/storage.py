from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol

from app.core.config import get_settings


class FileStore(Protocol):
    def put(self, bucket: str, path: str, data: bytes, content_type: str) -> None: ...

    def get(self, bucket: str, path: str) -> bytes | None: ...

    def signed_url(self, bucket: str, path: str, expires_in: int) -> str | None: ...


@dataclass
class MemoryFileStore:
    files: dict[tuple[str, str], bytes] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def put(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        with self._lock:
            self.files[(bucket, path)] = data

    def get(self, bucket: str, path: str) -> bytes | None:
        with self._lock:
            return self.files.get((bucket, path))

    def signed_url(self, bucket: str, path: str, expires_in: int) -> str | None:
        if self.get(bucket, path) is None:
            return None
        return f"/api/v1/files/{bucket}/{path}?ttl={expires_in}"

    def clear(self) -> None:
        with self._lock:
            self.files.clear()


class SupabaseFileStore:
    def __init__(self, url: str, service_key: str) -> None:
        from supabase import create_client

        self._client = create_client(url, service_key)

    def put(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        self._client.storage.from_(bucket).upload(
            path,
            data,
            {"content-type": content_type, "upsert": "true"},
        )

    def get(self, bucket: str, path: str) -> bytes | None:
        try:
            payload = self._client.storage.from_(bucket).download(path)
        except Exception:
            return None
        return bytes(payload) if payload is not None else None

    def signed_url(self, bucket: str, path: str, expires_in: int) -> str | None:
        try:
            result = self._client.storage.from_(bucket).create_signed_url(path, expires_in)
        except Exception:
            return None
        if isinstance(result, dict):
            return result.get("signedURL") or result.get("signedUrl")
        return getattr(result, "signed_url", None) or getattr(result, "signedURL", None)


_MEMORY_STORE = MemoryFileStore()


def get_file_store() -> FileStore:
    settings = get_settings()
    url = (settings.supabase_url or "").strip()
    secret = settings.supabase_service_role_key
    key = secret.get_secret_value().strip() if secret is not None else ""
    if url and key:
        return SupabaseFileStore(url, key)
    return _MEMORY_STORE


def reset_file_store() -> None:
    _MEMORY_STORE.clear()
