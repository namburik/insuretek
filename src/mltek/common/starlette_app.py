import contextlib
import logging
import os
import socket
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send


class CorrelationMiddleware:
    """Pure ASGI middleware that propagates X-Correlation-ID across the request lifecycle.

    Reads X-Correlation-ID from the incoming request header (or generates one),
    sets CorrelationContext for the duration of the request, echoes the ID back
    in the response header, and clears the context in a finally block.

    Use pure ASGI (not BaseHTTPMiddleware) so ContextVar changes propagate
    correctly into route handlers without the BaseHTTPMiddleware task-boundary
    isolation issue.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        from mltek.common.infra.context import CorrelationContext

        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        header_value = next(
            (v.decode() for k, v in raw_headers if k.lower() == b"x-correlation-id"),
            None,
        )
        correlation_id = header_value or CorrelationContext.generate()
        CorrelationContext.set(correlation_id)

        async def send_with_header(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-correlation-id", correlation_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            CorrelationContext.clear()


def streamable_http_routes(
    path: str,
    handle_request: Callable[[Scope, Receive, Send], Awaitable[None]],
) -> List[BaseRoute]:
    """Return Starlette routes for a StreamableHTTPSessionManager endpoint."""
    return [
        Mount(path, app=handle_request),
    ]



class StreamableStarletteApp:
    def __init__(
        self,
        server: Server,
        json_response: bool,
        path: str,
        health_path: Optional[str] = "/healthz",
        health_response: Optional[Dict[str, Any]] = None,
        debug: bool = False,
    ) -> None:
        self.session_manager = StreamableHTTPSessionManager(app=server, json_response=json_response)
        self.path = path
        self.health_path = health_path
        self.health_response = health_response or {"status": "ok"}
        self.debug = debug

    async def healthz(self, request: Request) -> JSONResponse:
        return JSONResponse(self.health_response)

    @contextlib.asynccontextmanager
    async def lifespan(self, app) -> AsyncIterator[None]:
        async with self.session_manager.run():
            yield

    def routes(self) -> List[BaseRoute]:
        routes = [*streamable_http_routes(self.path, self.session_manager.handle_request)]
        if self.health_path:
            routes.append(Route(self.health_path, endpoint=self.healthz, methods=["GET"]))
        return routes

    def create_app(self) -> Starlette:
        app = CorrelationMiddleware(create_starlette_app(routes=self.routes(), debug=self.debug, lifespan=self.lifespan))
        return apply_default_cors(app)


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles variant that disables HTTP caching for every response."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


def serve_html_no_cache(html_path: Path) -> Callable[[Request], HTMLResponse]:
    """Return a Starlette endpoint that serves an HTML file with no-cache headers."""

    async def _serve(request: Request) -> HTMLResponse:
        return HTMLResponse(
            html_path.read_text(),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    return _serve


def create_starlette_app(
    routes: List[Route],
    debug: bool = False,
    lifespan: Optional[Callable] = None,
) -> Starlette:
    """Create a configured Starlette app from a list of routes."""
    return Starlette(routes=routes, debug=debug, lifespan=lifespan)


def apply_default_cors(app: Starlette) -> CORSMiddleware:
    """Wrap a Starlette app in standard CORS middleware."""
    return CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        expose_headers=["Mcp-Session-Id"],
    )



_server_metadata_installed = False


def _attach_server_metadata_filter(host: str, port: int) -> None:
    global _server_metadata_installed
    if _server_metadata_installed:
        return
    node_name = os.getenv("NODE_NAME") or socket.gethostname()
    app_server = os.getenv("APP_SERVER") or f"{host}:{port}"
    _old_factory = logging.getLogRecordFactory()

    def _factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = _old_factory(*args, **kwargs)
        record.node = node_name  # type: ignore[attr-defined]
        record.app_server = app_server  # type: ignore[attr-defined]
        return record

    logging.setLogRecordFactory(_factory)
    _server_metadata_installed = True


def run_starlette_app(
    app: Starlette,
    host: str,
    port: int,
    access_log: bool = False,
    log_level: str = "info",
    **kwargs: Any,
) -> None:
    """Run a Starlette app with uvicorn using shared defaults."""
    import uvicorn

    _attach_server_metadata_filter(host, port)
    uvicorn.run(app, host=host, port=port, access_log=access_log, log_level=log_level, **kwargs)


def mount_no_cache_static(path: str, directory: Path, name: Optional[str] = None) -> Mount:
    """Create a no-cache static mount for Starlette."""
    return Mount(path, app=NoCacheStaticFiles(directory=directory), name=name)
