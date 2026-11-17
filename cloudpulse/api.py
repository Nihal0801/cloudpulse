"""Shared read-only dashboard routing for local HTTP and Lambda URLs."""

import json
from pathlib import Path

from cloudpulse.core import snapshot

WEB = Path(__file__).resolve().parent.parent / "web"
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
}


def route(path, method, targets, store, mode="aws"):
    headers = dict(HEADERS)
    if method not in ("GET", "HEAD"):
        return 405, {**headers, "Allow": "GET, HEAD"}, b"Method not allowed"
    if path in ("/api/status", "/healthz"):
        payload = (
            {"status": "ok", "mode": mode}
            if path == "/healthz"
            else {**snapshot(targets, store), "mode": mode}
        )
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    elif path in ("/", "/app.js", "/style.css", "/favicon.svg"):
        filename = "index.html" if path == "/" else path[1:]
        headers["Content-Type"] = {
            "html": "text/html; charset=utf-8",
            "js": "text/javascript; charset=utf-8",
            "css": "text/css; charset=utf-8",
            "svg": "image/svg+xml",
        }[filename.rsplit(".", 1)[1]]
        body = (WEB / filename).read_bytes()
    else:
        return 404, headers, b"Not found"
    return 200, headers, b"" if method == "HEAD" else body
