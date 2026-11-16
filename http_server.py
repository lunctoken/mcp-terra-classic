"""Run the same Terra Classic tools over Streamable HTTP at /mcp."""

import os

from mcp.server.transport_security import TransportSecuritySettings

from terra_classic_mcp.app import ALLOWED_ARGUMENTS, build_server
from terra_classic_mcp.core import LcdClient
from terra_classic_mcp.security import HTTPGuard


def _csv(name: str, default: str) -> list[str]:
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


def build_http_app():
    lcd = LcdClient()
    allowed_hosts = _csv("TERRA_CLASSIC_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*")
    allowed_origins = _csv("TERRA_CLASSIC_MCP_ALLOWED_ORIGINS", "http://127.0.0.1:*,http://localhost:*")
    transport = build_server(lcd).streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=1024 * 1024,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )
    return HTTPGuard(transport, set(ALLOWED_ARGUMENTS), lcd)


class LazyApp:
    """Validate HTTP security settings at ASGI startup, not during module import."""

    def __init__(self):
        self._app = None

    async def __call__(self, scope, receive, send):
        if self._app is None:
            self._app = build_http_app()
        await self._app(scope, receive, send)


app = LazyApp()
