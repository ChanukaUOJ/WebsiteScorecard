"""URL parsing utilities for website checks."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ParsedUrl:
    hostname: str
    port: int


def parse_url(url: str, default_port: int = 443) -> ParsedUrl:
    """Extract hostname and port from a URL or bare domain."""
    raw = url.strip()
    if not raw:
        raise ValueError("empty URL")

    if "://" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"invalid URL: {url!r}")

    port = parsed.port
    if port is None:
        scheme = parsed.scheme.lower()
        port = 80 if scheme == "http" else default_port
    return ParsedUrl(hostname=hostname, port=port)
