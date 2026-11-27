"""Shared fail-closed transport policy for Nexus registry and UI requests."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit


def nexus_transport_error(
    url: str | None,
    verify_tls: bool,
) -> tuple[str, str] | None:
    """Return a safe policy error for an invalid or insecure Nexus origin.

    HTTP is permitted only for literal loopback addresses and the reserved
    localhost name used by disposable integration environments. Remote HTTPS
    must verify the server certificate. DNS is deliberately not resolved here:
    mutable DNS answers must not change whether an origin is considered local.
    """
    if not url:
        return "nexus_url_invalid", "URL локального Nexus некорректен"

    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "nexus_url_invalid", "URL локального Nexus некорректен"

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or port == 0
    ):
        return "nexus_url_invalid", "URL локального Nexus некорректен"

    try:
        loopback = ip_address(host).is_loopback
    except ValueError:
        loopback = host.casefold().rstrip(".") == "localhost"

    if parsed.scheme == "http" and not loopback:
        return (
            "nexus_insecure_transport",
            "Удалённый Nexus требует HTTPS",
        )
    if parsed.scheme == "https" and not loopback and not verify_tls:
        return (
            "nexus_tls_verification_required",
            "Для удалённого Nexus обязательна проверка TLS",
        )
    return None
