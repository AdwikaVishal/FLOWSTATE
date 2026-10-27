"""The controlled invoice-processing demo site.

Only this site may be recorded or replayed. It is deliberately small,
deterministic and local: no authentication, no external requests and no
financial side effects. The same asset is served at ``/demo-app/`` from the API
process so Playwright and the console iframe share one source of truth.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

STATIC_DIR = Path(__file__).resolve().parent / "static"

router = APIRouter(prefix="/demo-app", tags=["demo-app"], include_in_schema=False)

_NO_STORE = {
    "Cache-Control": "no-store, max-age=0",
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'self'"
    ),
}


def _asset(name: str, media_type: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, media_type=media_type, headers=_NO_STORE)


@router.get("")
def demo_root() -> RedirectResponse:
    return RedirectResponse("/demo-app/", status_code=307)


@router.get("/")
def demo_index() -> FileResponse:
    return _asset("index.html", "text/html; charset=utf-8")


@router.get("/app.js")
def demo_script() -> FileResponse:
    return _asset("app.js", "text/javascript; charset=utf-8")


@router.get("/styles.css")
def demo_styles() -> FileResponse:
    return _asset("styles.css", "text/css; charset=utf-8")
