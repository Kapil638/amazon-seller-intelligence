from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse

# Hosts observed in normalized Product data / fixtures. Do not invent CDNs.
DEFAULT_ALLOWED_MEDIA_HOSTS: frozenset[str] = frozenset(
    {
        "m.media-amazon.com",
        "placehold.co",
    }
)

_BLOCKED_SCHEMES = frozenset({"file", "data", "ftp", "ftps", "javascript", "blob"})
_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".lan", ".corp", ".home", ".localhost")
_BLOCKED_HOSTS = frozenset({"localhost", "localhost.localdomain", "metadata.google.internal"})


class MediaUrlValidator:
    """Reject non-HTTPS and private/local URLs. Allow only known media hosts."""

    def __init__(self, allowed_hosts: frozenset[str] | None = None) -> None:
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else DEFAULT_ALLOWED_MEDIA_HOSTS

    def validate(self, url: str) -> tuple[bool, str | None, str | None]:
        """Return (ok, reason_if_rejected, hostname_if_parsed)."""
        raw = (url or "").strip()
        if not raw:
            return False, "empty_url", None
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "").lower()
        if scheme in _BLOCKED_SCHEMES:
            return False, f"blocked_scheme:{scheme}", None
        if scheme != "https":
            return False, "https_required", parsed.hostname
        if parsed.username or parsed.password:
            return False, "userinfo_not_allowed", parsed.hostname
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return False, "missing_host", None
        if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
            return False, "blocked_hostname", host
        if parsed.port not in (None, 443):
            return False, "blocked_port", host
        if _is_blocked_ip(host):
            return False, "private_or_local_ip", host
        if not _host_allowed(host, self.allowed_hosts):
            return False, "host_not_allowlisted", host
        return True, None, host


def allowed_hosts_from_settings(extra: str | None) -> frozenset[str]:
    hosts = set(DEFAULT_ALLOWED_MEDIA_HOSTS)
    if extra:
        for item in extra.split(","):
            cleaned = item.strip().lower().rstrip(".")
            if cleaned:
                hosts.add(cleaned)
    return frozenset(hosts)


def _host_allowed(host: str, allowed: frozenset[str]) -> bool:
    for candidate in allowed:
        if host == candidate or host.endswith("." + candidate):
            return True
    return False


def _is_blocked_ip(host: str) -> bool:
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
