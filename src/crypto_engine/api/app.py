"""FastAPI application factory (Phase 7, read-only).

``create_app(roots, settings)`` builds a GET-only application. Safety rails:

* every registered route must be GET-only (docs/self routes excepted) —
  asserted at build time, and re-asserted by tests;
* CORS is disabled unless ``settings.api.cors_origins`` is non-empty
  (explicit origins only; credentials never allowed);
* ``ApiError`` maps to sanitized JSON (no tracebacks, no paths, no secrets);
  unhandled exceptions become generic 500s with a request id for the logs;
* importing this module requires the ``api`` extra (fastapi); the error
  message tells the user exactly what to install.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from fastapi import APIRouter, FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    _FASTAPI_AVAILABLE = True
    _FASTAPI_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - exercised when extra is absent
    APIRouter = FastAPI = Request = JSONResponse = CORSMiddleware = None  # type: ignore[assignment]
    _FASTAPI_AVAILABLE = False
    _FASTAPI_IMPORT_ERROR = exc

from crypto_engine.ai.env_loader import load_env
from crypto_engine.api.schemas import fail, utc_now_iso
from crypto_engine.api.security import ApiError, get_api_token, verify_api_token

# Load .env at module import time so AI credentials are available when the
# API server starts.
load_env()

DOCS_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
AUTH_SKIP_PATHS = DOCS_PATHS | {
    "/favicon.ico",
    "/health",
    "/api/v1/health",
    "/dashboard/monitor/",
    "/dashboard/monitor/monitor.css",
    "/dashboard/monitor/monitor.js",
}

# Production authentication policy. When SIGNALFORGE_AUTH_REQUIRED=1, the
# application refuses to serve unless SIGNALFORGE_API_TOKEN is configured.
# Without this flag, development mode is assumed: auth is enforced only when
# a token is configured, and skipped otherwise (explicit opt-in for production).
AUTH_REQUIRED = os.environ.get("SIGNALFORGE_AUTH_REQUIRED", "").strip() == "1"


def require_fastapi() -> None:
    """Raise an actionable error when the ``api`` extra is not installed."""
    if not _FASTAPI_AVAILABLE:
        raise ImportError(
            "The API/dashboard requires the optional 'api' extra: "
            "run `uv sync --extra api` (installs fastapi + uvicorn). "
            "The core quant CLI works without it."
        ) from _FASTAPI_IMPORT_ERROR


def _walk_routes(root: Any) -> list[tuple[str, set[str]]]:
    """Flatten routes, descending into included routers and mounts."""
    found: list[tuple[str, set[str]]] = []

    def _visit(node: Any, prefix: str = "") -> None:
        routes = getattr(node, "routes", None)
        if isinstance(node, (list, tuple)):
            for child in node:
                _visit(child, prefix)
            return
        if routes is None:
            original = getattr(node, "original_router", None)
            if original is not None:
                _visit(original, prefix)
            return
        for route in routes:
            path = getattr(route, "path", None)
            methods = set(getattr(route, "methods", set()) or set())
            if path is not None and methods:
                found.append((prefix + path, methods))
            else:
                for attr in ("original_router", "router", "routes", "app"):
                    child = getattr(route, attr, None)
                    if child is not None and child is not node:
                        _visit(child, prefix)
                        break

    _visit(root)
    return found


def list_data_routes(app: Any) -> list[tuple[str, set[str]]]:
    """All registered data routes as ``(path, methods)`` (docs excluded)."""
    return [
        (path, methods)
        for path, methods in _walk_routes(getattr(app, "router", app))
        if path not in DOCS_PATHS and not path.endswith("/openapi.json")
    ]


def assert_get_only(app: Any) -> None:
    """Fail closed if any non-GET data route is registered."""
    offenders = [
        f"{sorted(methods)} {path}" for path, methods in list_data_routes(app) if methods != {"GET"}
    ]
    if offenders:
        raise RuntimeError(f"Non-GET routes registered (refusing to serve): {offenders}")


def create_app(roots: Any, settings: Any) -> Any:
    """Build the read-only FastAPI application (no side effects on build)."""
    require_fastapi()

    from crypto_engine.config.settings import validate_deployment_safety

    _create_app_violations = validate_deployment_safety(settings)
    if _create_app_violations:
        raise RuntimeError("; ".join(_create_app_violations))

    app: Any = FastAPI(
        title="SignalForge API",
        version="1.0.0",
        description=(
            "Read-only research API over canonical SignalForge records. "
            "No orders, no training, no fetching, no mutations."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )
    router: Any = APIRouter()

    from crypto_engine.api import routes as route_table

    route_table.register(router, roots, settings)

    from crypto_engine.dashboard import routes as dashboard_routes

    dashboard_routes.register(router, roots, settings)

    from crypto_engine.api import progress_routes

    progress_routes.register(router, roots, settings)
    app.include_router(router)

    origins = list(getattr(settings.api, "cors_origins", []) or [])
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["Accept", "Content-Type"],
            max_age=600,
        )

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Any, exc: ApiError) -> Any:
        request_id = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=exc.status_code, content=fail(exc.code, exc.message, request_id or None)
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Any, exc: Exception) -> Any:
        import uuid

        request_id = uuid.uuid4().hex
        return JSONResponse(
            status_code=500,
            content=fail("internal_error", f"Unexpected failure (ref {request_id}).", request_id),
        )

    try:
        from starlette.middleware.base import BaseHTTPMiddleware

        async def _auth_dispatch(request: Request, call_next):
            token = get_api_token()
            if request.url.path not in AUTH_SKIP_PATHS:
                if AUTH_REQUIRED and token is None:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "error": "unauthorized",
                            "message": "API authentication required but no token configured.",
                        },
                    )
                if token is not None:
                    try:
                        verify_api_token(request.headers.get("authorization"))
                    except ApiError:
                        return JSONResponse(
                            status_code=401,
                            content={
                                "error": "unauthorized",
                                "message": "Missing or invalid API token.",
                            },
                        )
            return await call_next(request)

        app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_dispatch)
    except ImportError:
        pass  # starlette absent: auth middleware is optional

    # Fail closed: when AUTH_REQUIRED is set but no API token is configured,
    # refuse to serve. Development mode (AUTH_REQUIRED unset) allows auth to
    # be skipped when no token is configured.
    if AUTH_REQUIRED and get_api_token() is None:
        raise RuntimeError(
            "SIGNALFORGE_AUTH_REQUIRED=1 but SIGNALFORGE_API_TOKEN is not set. "
            "Configure SIGNALFORGE_API_TOKEN or unset SIGNALFORGE_AUTH_REQUIRED "
            "for development mode."
        )

    assert_get_only(app)
    app.state.signalforge_roots = roots
    app.state.signalforge_settings = settings
    app.state.started_at = utc_now_iso()
    return app


__all__ = ["DOCS_PATHS", "assert_get_only", "create_app", "list_data_routes", "require_fastapi"]
