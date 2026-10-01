"""Minimal asyncio HTTP/1.1 server for the Reader Mini App.

Runs inside the bot's event loop so SQLite and the upload services stay single-threaded.
It binds loopback by default and expects a TLS reverse proxy in front of it.
"""

import asyncio
import logging
from contextlib import suppress
from urllib.parse import unquote, urlsplit

from .media import FileSlice

log = logging.getLogger(__name__)
MAX_HEAD = 16 * 1024
MAX_BODY = 1024 * 1024
READ_TIMEOUT = 20
REASONS = {
    200: "OK",
    206: "Partial Content",
    416: "Range Not Satisfiable",
    400: "Bad Request",
    401: "Unauthorized",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def response_head(status: int, headers: dict, length: int) -> bytes:
    lines = [f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}"]
    lines += [f"{key}: {value}" for key, value in headers.items()]
    lines += [f"Content-Length: {length}", "Connection: close", "", ""]
    return "\r\n".join(lines).encode("latin-1", "replace")


def response(status: int, headers: dict, body: bytes) -> bytes:
    return response_head(status, headers, len(body)) + body


def parse_head(head: bytes) -> tuple[str, str, dict]:
    lines = head.decode("latin-1").split("\r\n")
    method, target, _ = (lines[0].split(" ") + ["", "", ""])[:3]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    return method.upper(), unquote(urlsplit(target).path), headers


class ReaderServer:
    def __init__(self, api, host: str = "127.0.0.1", port: int = 8081):
        self.api, self.host, self.port = api, host, port
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.connection, self.host, self.port)
        log.info("stage=reader-server host=%s port=%s", self.host, self.port)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            status, headers, body = await self.exchange(reader)
            if isinstance(body, FileSlice):
                async with asyncio.timeout(300):
                    with body.path.open("rb") as audio:
                        audio.seek(body.offset)
                        writer.write(response_head(status, headers, body.length))
                        await writer.drain()
                        remaining = 0 if body.head_only else body.length
                        while remaining:
                            block = await asyncio.to_thread(audio.read, min(64 * 1024, remaining))
                            if not block:
                                break
                            writer.write(block)
                            await writer.drain()
                            remaining -= len(block)
            else:
                writer.write(response(status, headers, body))
                await writer.drain()
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:
            log.error("stage=reader-connection exception_type=%s", type(exc).__name__)
        finally:
            writer.close()
            with suppress(ConnectionError, TimeoutError):
                await writer.wait_closed()

    async def exchange(self, reader: asyncio.StreamReader):
        async with asyncio.timeout(READ_TIMEOUT):
            head = await reader.readuntil(b"\r\n\r\n")
            if len(head) > MAX_HEAD:
                return 413, {}, b""
            method, path, headers = parse_head(head[:-4])
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                return 400, {}, b"Malformed request."
            if not 0 <= length <= MAX_BODY:
                return 413, {}, b"Request body too large."
            body = await reader.readexactly(length) if length else b""
        return await self.api.dispatch(method, path, headers, body)
