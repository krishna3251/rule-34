"""Bounded public-network HTTP requests for user-supplied URLs."""
from __future__ import annotations

import ipaddress
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import aiohttp
from aiohttp.resolver import DefaultResolver


class UnsafeURL(ValueError):
    pass


def validate_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or port not in {None, 80, 443}):
            raise UnsafeURL("Use a public HTTP(S) URL on a standard port.")
        host = parsed.hostname.rstrip('.').lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            raise UnsafeURL("Private hosts are not allowed.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise UnsafeURL("Private addresses are not allowed.")
        return url
    except (ValueError, TypeError) as exc:
        raise UnsafeURL("Use a valid public HTTP(S) URL.") from exc


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await super().resolve(host, port, family)
        if not rows or any(not ipaddress.ip_address(row['host']).is_global for row in rows):
            raise UnsafeURL("The host must resolve only to public addresses.")
        # These exact validated addresses are used by the connector (no second DNS lookup).
        return rows


@asynccontextmanager
async def public_get(url: str, **kwargs):
    validate_url(url)
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    timeout = kwargs.pop('timeout', aiohttp.ClientTimeout(total=20))
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
        async with session.get(url, allow_redirects=False, **kwargs) as response:
            if 300 <= response.status < 400:
                raise UnsafeURL("Redirects are not accepted for submitted URLs.")
            yield response


async def read_bounded(response, limit: int) -> bytes:
    data = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("Response exceeds the permitted size.")
    return bytes(data)
