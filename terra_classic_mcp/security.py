"""HTTP authentication and resource limits, independent of the MCP client."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import time

from .monitoring import METRICS, logger


def _positive_setting(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


class HTTPGuard:
    def __init__(self, app, allowed_tools: set[str], lcd=None):
        self.app = app
        self.allowed_tools = allowed_tools
        self.lcd = lcd
        self.mode = os.environ.get("TERRA_CLASSIC_MCP_AUTH_MODE", "bearer")
        if self.mode not in {"bearer", "local"}:
            raise ValueError("TERRA_CLASSIC_MCP_AUTH_MODE must be bearer or local")
        self.credentials = []
        if self.mode == "bearer":
            path = os.environ.get("TERRA_CLASSIC_MCP_TOKENS_FILE")
            if not path:
                raise ValueError("TERRA_CLASSIC_MCP_TOKENS_FILE is required for HTTP")
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                raise ValueError("The MCP credentials file must contain clients")
            seen = set()
            for client, config in data.items():
                if not isinstance(client, str) or not client or not isinstance(config, dict):
                    raise ValueError("Invalid MCP credentials file")
                token = config.get("token")
                tools = config.get("tools")
                if not isinstance(token, str) or len(token) < 32 or any(char.isspace() for char in token):
                    raise ValueError("MCP bearer tokens must contain at least 32 non-whitespace characters")
                if not isinstance(tools, list) or not tools or not all(isinstance(tool, str) and tool in allowed_tools for tool in tools):
                    raise ValueError("Each MCP client needs an explicit non-empty tool allowlist")
                digest = hashlib.sha256(token.encode()).digest()
                if digest in seen:
                    raise ValueError("Duplicate MCP bearer token")
                seen.add(digest)
                self.credentials.append((digest, client, frozenset(tools)))
        self.max_body = 1024 * 1024
        self.max_concurrent = _positive_setting("TERRA_CLASSIC_MCP_MAX_CONCURRENT", 8)
        self.max_per_minute = _positive_setting("TERRA_CLASSIC_MCP_MAX_REQUESTS_PER_MINUTE", 60)
        self.max_expensive = _positive_setting("TERRA_CLASSIC_MCP_MAX_EXPENSIVE_PER_MINUTE", 6)
        self.max_global_per_minute = _positive_setting("TERRA_CLASSIC_MCP_MAX_GLOBAL_REQUESTS_PER_MINUTE", 240)
        self.max_global_expensive = _positive_setting("TERRA_CLASSIC_MCP_MAX_GLOBAL_EXPENSIVE_PER_MINUTE", 24)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._calls = defaultdict(deque)
        self._expensive = defaultdict(deque)
        self._global_calls = defaultdict(deque)
        self._global_expensive = defaultdict(deque)

    @staticmethod
    async def _reply(send, status: int, message: str, *, authenticate: bool = False):
        body = json.dumps({"error": message}, separators=(",", ":")).encode()
        headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
        if authenticate:
            headers.append((b"www-authenticate", b'Bearer realm="terra-classic-mcp"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    def _principal(self, scope):
        if self.mode == "local":
            host = (scope.get("client") or ("", 0))[0]
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return "local", frozenset(self.allowed_tools)
            except ValueError:
                pass
            return None
        values = [value for name, value in scope.get("headers") or [] if name.lower() == b"authorization"]
        if len(values) != 1 or len(values[0]) > 512:
            return None
        authorization = values[0]
        if not authorization.startswith(b"Bearer "):
            return None
        digest = hashlib.sha256(authorization[7:]).digest()
        match = None
        for expected, client, tools in self.credentials:
            if hmac.compare_digest(digest, expected):
                match = (client, tools)
        return match

    @staticmethod
    def _within_limit(bucket, key, maximum):
        now = time.monotonic()
        entries = bucket[key]
        while entries and entries[0] <= now - 60:
            entries.popleft()
        if len(entries) >= maximum:
            return False
        entries.append(now)
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.monotonic()
        status = 500

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self._dispatch(scope, receive, observed_send)
        finally:
            METRICS.http_request(status, time.monotonic() - start)
            if status >= 400:
                logger.warning('{"event":"http_request","status":%d}', status)

    @staticmethod
    def _loopback(scope):
        host = (scope.get("client") or ("", 0))[0]
        hosts = [value.decode("ascii", "ignore").lower() for name, value in scope.get("headers") or [] if name.lower() == b"host"]
        if len(hosts) != 1 or not any(hosts[0] == local or hosts[0].startswith(local + ":") for local in ("localhost", "127.0.0.1", "[::1]")):
            return False
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @staticmethod
    async def _plain(send, status: int, body: bytes, content_type: bytes):
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", content_type), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    async def _dispatch(self, scope, receive, send):
        path = scope.get("path")
        if path in {"/healthz", "/readyz", "/metrics"}:
            if scope.get("method") != "GET" or not self._loopback(scope):
                await self._reply(send, 404, "Not found")
                return
            if path == "/healthz":
                await self._plain(send, 200, b"ok\n", b"text/plain; charset=utf-8")
            elif path == "/readyz":
                try:
                    if self.lcd is None:
                        raise ValueError("LCD client unavailable")
                    await asyncio.to_thread(self.lcd._check_status)
                    await self._plain(send, 200, b"ready\n", b"text/plain; charset=utf-8")
                except Exception:
                    await self._plain(send, 503, b"unavailable\n", b"text/plain; charset=utf-8")
            else:
                await self._plain(send, 200, METRICS.prometheus(), b"text/plain; version=0.0.4; charset=utf-8")
            return
        if path != "/mcp":
            await self._reply(send, 404, "Not found")
            return
        principal = self._principal(scope)
        if principal is None:
            await self._reply(send, 401, "Authentication required", authenticate=True)
            return
        client, tools = principal
        if self._semaphore.locked():
            await self._reply(send, 429, "MCP concurrency limit exceeded")
            return
        await self._semaphore.acquire()
        try:
            await self._authorized(scope, receive, send, client, tools)
        finally:
            self._semaphore.release()

    async def _authorized(self, scope, receive, send, client, tools):
        chunks = []
        size = 0
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            try:
                message = await asyncio.wait_for(receive(), timeout=max(0, deadline - asyncio.get_running_loop().time()))
            except asyncio.TimeoutError:
                await self._reply(send, 408, "MCP request body timed out")
                return
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body:
                await self._reply(send, 413, "MCP request is too large")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        method = None
        tool = None
        batch = False
        if scope.get("method") == "POST":
            try:
                payload = json.loads(body)
                if isinstance(payload, list):
                    batch = True
                if isinstance(payload, dict):
                    method = payload.get("method")
                    if method == "tools/call":
                        tool = payload.get("params", {}).get("name")
            except (ValueError, TypeError, AttributeError):
                pass  # The MCP transport reports malformed JSON-RPC.
        if not self._within_limit(self._calls, client, self.max_per_minute):
            await self._reply(send, 429, "MCP request quota exceeded")
            return
        if not self._within_limit(self._global_calls, "all", self.max_global_per_minute):
            await self._reply(send, 429, "MCP global request quota exceeded")
            return
        if batch:
            await self._reply(send, 400, "JSON-RPC batches are not accepted")
            return
        if method == "tools/call" and tool not in tools:
            await self._reply(send, 403, "Tool is not permitted for this client")
            return
        if tool in {"terra_get_validators", "terra_get_validator_page", "terra_get_burn_snapshot",
                    "terra_get_authz_grants_received", "terra_explain_transaction",
                    "terra_query_contract", "terra_get_cw20_token_info",
                    "terra_get_cw20_balance", "terra_compare_chain_status", "terra_compare_balance",
                    "terra_get_ibc_packet_observation"}:
            if not self._within_limit(self._expensive, client, self.max_expensive):
                await self._reply(send, 429, "Expensive tool quota exceeded")
                return
            if not self._within_limit(self._global_expensive, "all", self.max_global_expensive):
                await self._reply(send, 429, "Global expensive tool quota exceeded")
                return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)
