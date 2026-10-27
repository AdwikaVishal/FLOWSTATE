"""HTTP client for the FLOWSTATE backend.

Only environment configuration supplies the base URL and token. Arguments are
validated here and again by the backend. Tokens are never accepted as tool
arguments, logged, or included in an error message.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2


class AdapterError(Exception):
    """A typed failure that can be returned to Alan without leaking secrets."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status: int = 0,
        request_id: str | None = None,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.request_id = request_id
        self.retryable = retryable
        self.details = dict(details or {})


@dataclass
class AlanConfig:
    base_url: str = field(default_factory=lambda: os.getenv("FLOWSTATE_API_URL", DEFAULT_BASE_URL))
    token: str | None = field(default_factory=lambda: os.getenv("FLOWSTATE_API_TOKEN"))
    actor_id: str = field(
        default_factory=lambda: os.getenv("FLOWSTATE_ALAN_ACTOR", "alan-operator")
    )
    actor_role: str = field(default_factory=lambda: os.getenv("FLOWSTATE_ALAN_ROLE", "OPERATOR"))
    actor_kind: str = field(default_factory=lambda: os.getenv("FLOWSTATE_ALAN_ACTOR_KIND", "HUMAN"))
    timeout: float = field(
        default_factory=lambda: float(os.getenv("FLOWSTATE_ALAN_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))
    )
    retries: int = field(
        default_factory=lambda: int(os.getenv("FLOWSTATE_ALAN_RETRIES", DEFAULT_RETRIES))
    )


def validate_base_url(value: str) -> str:
    text = value.strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AdapterError(
            "INVALID_BASE_URL",
            "FLOWSTATE_API_URL must be an absolute http(s) URL.",
        )
    if parsed.username or parsed.password:
        raise AdapterError(
            "INVALID_BASE_URL",
            "FLOWSTATE_API_URL must not embed credentials.",
        )
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not loopback:
        raise AdapterError(
            "INSECURE_BASE_URL",
            "A non-loopback FLOWSTATE_API_URL must use https.",
        )
    if not text.endswith("/api/v1"):
        text = f"{text}/api/v1"
    return text


class FlowstateClient:
    """Synchronous, retry-aware client used by both the CLI and MCP tools."""

    def __init__(
        self,
        config: AlanConfig | None = None,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        self.config = config or AlanConfig()
        self.base_url = validate_base_url(self.config.base_url)
        self._http = http or httpx.Client(timeout=self.config.timeout)
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> FlowstateClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "flowstate-alan/1",
            "X-Flowstate-Actor": self.config.actor_id,
            "X-Flowstate-Actor-Kind": self.config.actor_kind,
            "X-Flowstate-Role": self.config.actor_role,
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _problem(response: httpx.Response) -> AdapterError:
        request_id = response.headers.get("X-Request-ID")
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        return AdapterError(
            str(body.get("code") or f"HTTP_{response.status_code}"),
            str(body.get("detail") or f"The backend returned HTTP {response.status_code}."),
            status=response.status_code,
            request_id=request_id,
            retryable=response.status_code in {429, 502, 503, 504},
            details=body.get("errors") if isinstance(body.get("errors"), dict) else None,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        retry: bool | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        method_upper = method.upper()
        retry_allowed = (
            method_upper == "GET" if retry is None else retry
        ) or idempotency_key is not None
        attempts = max(self.config.retries, 0) + 1 if retry_allowed else 1
        last_error: AdapterError | None = None
        for attempt in range(attempts):
            try:
                response = self._http.request(
                    method_upper,
                    url,
                    json=dict(json_body) if json_body is not None else None,
                    params=dict(params) if params else None,
                    headers=self._headers(idempotency_key),
                    timeout=self.config.timeout,
                )
            except httpx.TimeoutException as exc:
                last_error = AdapterError(
                    "BACKEND_TIMEOUT",
                    "The FLOWSTATE backend did not respond before the timeout.",
                    retryable=retry_allowed,
                )
                if attempt + 1 < attempts and retry_allowed:
                    time.sleep(min(0.2 * (2**attempt), 2.0))
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = AdapterError(
                    "BACKEND_UNREACHABLE",
                    f"The FLOWSTATE backend could not be reached: {type(exc).__name__}.",
                    retryable=retry_allowed,
                )
                if attempt + 1 < attempts and retry_allowed:
                    time.sleep(min(0.2 * (2**attempt), 2.0))
                    continue
                raise last_error from exc

            if response.status_code >= 500 and attempt + 1 < attempts and retry_allowed:
                time.sleep(min(0.2 * (2**attempt), 2.0))
                continue
            if response.status_code >= 400:
                raise self._problem(response)
            if response.status_code == 204:
                return {}
            try:
                return response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise AdapterError(
                    "MALFORMED_BACKEND_RESPONSE",
                    "The backend returned a response that was not valid JSON.",
                    status=response.status_code,
                    request_id=response.headers.get("X-Request-ID"),
                ) from exc
        if last_error is not None:
            raise last_error
        raise AdapterError("BACKEND_ERROR", "The backend request failed.")

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(
        self,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        return self.request("POST", path, json_body=body or {}, idempotency_key=idempotency_key)

    def get_bytes(self, path: str) -> bytes:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self._http.get(url, headers=self._headers(), timeout=self.config.timeout)
        except httpx.HTTPError as exc:
            raise AdapterError(
                "BACKEND_UNREACHABLE",
                f"The FLOWSTATE backend could not be reached: {type(exc).__name__}.",
            ) from exc
        if response.status_code >= 400:
            raise self._problem(response)
        return response.content
