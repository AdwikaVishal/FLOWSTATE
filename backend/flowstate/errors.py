"""Consistent RFC 9457 problem+json error responses."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_CONTENT_TYPE = "application/problem+json"


class AppError(Exception):
    """Base class for expected, client-facing failures."""

    status_code = 400
    code = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"


class DemoSeedDisabledError(AppError):
    status_code = 403
    code = "DEMO_SEED_DISABLED"


def problem_body(
    *,
    code: str,
    message: str,
    status_code: int,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"https://flowstate.dev/problems/{code.lower()}",
        "title": code.replace("_", " ").title(),
        "status": status_code,
        "code": code,
        "detail": message,
    }
    if request_id:
        body["instance"] = f"urn:request:{request_id}"
    if details:
        body["errors"] = details
    return body


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            media_type=PROBLEM_CONTENT_TYPE,
            content=problem_body(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                request_id=_request_id(request),
                details=exc.details or None,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = {
            ".".join(str(part) for part in err["loc"][1:]): err["msg"] for err in exc.errors()
        }
        return JSONResponse(
            status_code=422,
            media_type=PROBLEM_CONTENT_TYPE,
            content=problem_body(
                code="VALIDATION_FAILED",
                message="The request payload did not validate.",
                status_code=422,
                request_id=_request_id(request),
                details=errors,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            media_type=PROBLEM_CONTENT_TYPE,
            content=problem_body(
                code=f"HTTP_{exc.status_code}",
                message=str(exc.detail),
                status_code=exc.status_code,
                request_id=_request_id(request),
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request.app.state.logger.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            media_type=PROBLEM_CONTENT_TYPE,
            content=problem_body(
                code="INTERNAL_ERROR",
                message="An unexpected error occurred. The request id is in the audit log.",
                status_code=500,
                request_id=_request_id(request),
            ),
        )
