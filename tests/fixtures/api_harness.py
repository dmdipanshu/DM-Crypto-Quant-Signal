"""Stdlib ASGI test harness (Phase 7).

No httpx/TestClient dependency: drives the FastAPI/Starlette app object
directly with ``asyncio`` and captures status/headers/body. Offline,
deterministic, single-process.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


def _run(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        raise RuntimeError("api_request must be called outside a running event loop")
    return asyncio.run(coro)


async def _call(
    app: Any, method: str, path: str, query: str = "", body: bytes = b""
) -> tuple[int, dict[str, str], bytes]:
    status_holder: dict[str, int] = {}
    headers_out: dict[str, str] = {}
    chunks: list[bytes] = []
    sent_request = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status_holder["status"] = int(message["status"])
            headers_out.update(
                {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in message.get("headers", [])
                }
            )
        elif message["type"] == "http.response.body":
            chunks.append(bytes(message.get("body", b"")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": query.encode("latin-1"),
        "headers": [(b"host", b"testserver"), (b"accept", b"application/json")],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "state": {},
    }
    await app(scope, receive, send)
    return status_holder.get("status", 0), headers_out, b"".join(chunks)


def _split(path: str) -> tuple[str, str]:
    if "?" in path:
        head, tail = path.split("?", 1)
        return head, tail
    return path, ""


def api_get(app: Any, path: str, query: str = "") -> tuple[int, dict[str, str], Any]:
    """GET ``path``; returns ``(status, headers, parsed_json_or_text)``."""
    head, tail = _split(path)
    status, headers, raw = _run(_call(app, "GET", head, query or tail))
    ctype = headers.get("content-type", "")
    if "application/json" in ctype and raw:
        return status, headers, json.loads(raw.decode("utf-8"))
    return status, headers, raw.decode("utf-8", errors="replace")


def api_request(
    app: Any, method: str, path: str, query: str = "", body: bytes = b""
) -> tuple[int, dict[str, str], Any]:
    """Any-method variant (used to prove non-GET routes are absent → 405)."""
    head, tail = _split(path)
    status, headers, raw = _run(_call(app, method, head, query or tail, body))
    ctype = headers.get("content-type", "")
    if "application/json" in ctype and raw:
        try:
            return status, headers, json.loads(raw.decode("utf-8"))
        except ValueError:
            pass
    return status, headers, raw.decode("utf-8", errors="replace")


__all__ = ["api_get", "api_request"]
