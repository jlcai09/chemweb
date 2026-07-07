from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


Compress = Callable[..., bytes]
CompressorFactory = Callable[..., Any]
COMPRESSIBLE_TYPES = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/xhtml+xml",
    "image/svg+xml",
    "application/vnd.chemssh.structure+bin",
}
STREAMING_TYPES = {
    "text/event-stream",
}


class BrotliMiddleware:
    def __init__(self, app: ASGIApp, *, enabled: bool = True, quality: int = 1) -> None:
        self.app = app
        self.enabled = enabled
        self.quality = quality
        self.compress: Compress | None = None
        self.compressor_factory: CompressorFactory | None = None

        if enabled:
            try:
                import brotli
            except ImportError as exc:  # pragma: no cover - depends on install state
                raise RuntimeError("Brotli compression is enabled but the 'Brotli' package is not installed.") from exc
            self.compress = brotli.compress
            self.compressor_factory = brotli.Compressor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled or self.compress is None or self.compressor_factory is None:
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        if scope.get("method") == "HEAD" or not _accepts_brotli(request_headers.get("accept-encoding", "")):
            await self.app(scope, receive, send)
            return

        responder = _BrotliResponder(self.app, send, self.compress, self.compressor_factory, self.quality)
        await responder(scope, receive)


class _BrotliResponder:
    def __init__(
        self,
        app: ASGIApp,
        send: Send,
        compress: Compress,
        compressor_factory: CompressorFactory,
        quality: int,
    ) -> None:
        self.app = app
        self.send = send
        self.compress = compress
        self.compressor_factory = compressor_factory
        self.quality = quality
        self.initial_message: Message | None = None
        self.body_parts: list[bytes] = []
        self.streaming = False
        self.stream_compressor: Any | None = None

    async def __call__(self, scope: Scope, receive: Receive) -> None:
        await self.app(scope, receive, self.send_with_brotli)

    async def send_with_brotli(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.initial_message = message
            headers = MutableHeaders(raw=list(message["headers"]))
            if _should_stream_compress(message["status"], headers):
                self.streaming = True
                self.stream_compressor = self.compressor_factory(quality=self.quality)
                headers["Content-Encoding"] = "br"
                if "Content-Length" in headers:
                    del headers["Content-Length"]
                _set_vary_accept_encoding(headers)
                message["headers"] = headers.raw
                await self.send(message)
            return

        if message["type"] != "http.response.body":
            await self.send(message)
            return

        if self.streaming:
            await self.send_streaming_body(message)
            return

        body = message.get("body", b"")
        if body:
            self.body_parts.append(body)

        if message.get("more_body", False):
            return

        await self.send_final_body()

    async def send_streaming_body(self, message: Message) -> None:
        compressor = self.stream_compressor
        if compressor is None:
            await self.send(message)
            return

        body = message.get("body", b"")
        compressed = b""
        if body:
            compressed += compressor.process(body)
            compressed += compressor.flush()
        if not message.get("more_body", False):
            compressed += compressor.finish()

        await self.send({
            "type": "http.response.body",
            "body": compressed,
            "more_body": message.get("more_body", False),
        })

    async def send_final_body(self) -> None:
        body = b"".join(self.body_parts)
        start = self.initial_message

        if start is None:
            await self.send({"type": "http.response.body", "body": body, "more_body": False})
            return

        headers = MutableHeaders(raw=list(start["headers"]))
        if not _should_compress(start["status"], headers, body):
            await self.send(start)
            await self.send({"type": "http.response.body", "body": body, "more_body": False})
            return

        compressed = self.compress(body, quality=self.quality)
        headers["Content-Encoding"] = "br"
        headers["Content-Length"] = str(len(compressed))
        _set_vary_accept_encoding(headers)
        start["headers"] = headers.raw

        await self.send(start)
        await self.send({"type": "http.response.body", "body": compressed, "more_body": False})


def _accepts_brotli(header_value: str) -> bool:
    brotli_q: float | None = None
    wildcard_q: float | None = None

    for part in header_value.split(","):
        encoding, *parameters = part.strip().split(";")
        name = encoding.strip().lower()
        if name not in {"br", "*"}:
            continue

        q = 1.0
        for parameter in parameters:
            key, _, value = parameter.strip().partition("=")
            if key.lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0

        if name == "br":
            brotli_q = q
        else:
            wildcard_q = q

    if brotli_q is not None:
        return brotli_q > 0
    return wildcard_q is not None and wildcard_q > 0


def _should_compress(status_code: int, headers: MutableHeaders, body: bytes) -> bool:
    if not body or status_code < 200 or status_code in {204, 206, 304}:
        return False
    if headers.get("Content-Encoding"):
        return False

    content_type = headers.get("Content-Type", "").split(";", 1)[0].lower()
    if not content_type:
        return False
    if content_type in STREAMING_TYPES:
        return False

    return (
        content_type.startswith("text/")
        or content_type in COMPRESSIBLE_TYPES
        or content_type.endswith("+json")
        or content_type.endswith("+xml")
    )


def _should_stream_compress(status_code: int, headers: MutableHeaders) -> bool:
    if status_code < 200 or status_code in {204, 206, 304}:
        return False
    if headers.get("Content-Encoding"):
        return False
    content_type = headers.get("Content-Type", "").split(";", 1)[0].lower()
    return content_type in STREAMING_TYPES


def _set_vary_accept_encoding(headers: MutableHeaders) -> None:
    current = headers.get("Vary")
    if not current:
        headers["Vary"] = "Accept-Encoding"
        return

    values = [value.strip().lower() for value in current.split(",")]
    if "accept-encoding" not in values and "*" not in values:
        headers["Vary"] = f"{current}, Accept-Encoding"
