"""Bounded streaming HTTP, with public-address checks on every redirect."""

import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit

import httpx

from .models import UserError


async def check_public_url(url: str) -> None:
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise UserError("The podcast supplied an invalid HTTP URL.")
    if p.port not in {None, 80, 443}:
        raise UserError("The podcast supplied an unsupported network port.")
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, p.hostname, p.port or 443)
    except OSError:
        raise UserError("Couldn't reach the podcast host. Try again later.") from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise UserError("The podcast supplied a non-public network address.")


@asynccontextmanager
async def stream(client: httpx.AsyncClient, url: str):
    for _ in range(8):
        await check_public_url(url)
        async with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                if not response.headers.get("location"):
                    raise UserError("The podcast host returned an invalid redirect.")
                url = urljoin(str(response.url), response.headers["location"])
                continue
            response.raise_for_status()
            yield response
            return
    raise UserError("The podcast host returned too many redirects.")


async def fetch(client: httpx.AsyncClient, url: str, limit: int = 12_000_000) -> bytes:
    data = bytearray()
    async with stream(client, url) as response:
        async for chunk in response.aiter_bytes(64 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                raise UserError("Podcast metadata exceeds the safety size limit.")
    return bytes(data)


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(60, connect=20),
        headers={"User-Agent": "PersonalPodcastBot/0.1"},
        trust_env=False,
    )
