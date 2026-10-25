from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from app.core.exceptions import UnsafeURLError


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeURLError("Only public HTTP and HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs containing credentials are not allowed")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise UnsafeURLError("Local network URLs are not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise UnsafeURLError("URL hostname could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if any(
            (
                ip.is_private,
                ip.is_loopback,
                ip.is_link_local,
                ip.is_multicast,
                ip.is_reserved,
                ip.is_unspecified,
            )
        ):
            raise UnsafeURLError("Private or reserved network destinations are not allowed")
    return url


async def fetch_public_document(
    url: str, max_bytes: int, timeout_seconds: int
) -> tuple[bytes, str]:
    safe_url = validate_public_url(url)
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        current = safe_url
        for _ in range(4):
            response = await client.get(current, headers={"User-Agent": "AdvanceRAG/1.0"})
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise UnsafeURLError("Redirect response omitted a destination")
                current = validate_public_url(str(response.url.join(location)))
                continue
            response.raise_for_status()
            length = int(response.headers.get("content-length", "0") or 0)
            if length > max_bytes or len(response.content) > max_bytes:
                raise UnsafeURLError("Remote document exceeds the configured size limit")
            content_type = response.headers.get("content-type", "text/html").split(";", 1)[0]
            return response.content, content_type
    raise UnsafeURLError("Too many URL redirects")
