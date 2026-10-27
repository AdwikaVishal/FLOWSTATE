"""Structured logging with a redaction filter.

Logs are JSON lines in non-test environments and never contain token-shaped
values. The filter is deliberately blunt: a false positive is cheaper than a
leaked credential.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

REDACTED = "[redacted]"

_SENSITIVE_KEYS = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|card|cvv|iban|account)",
    re.IGNORECASE,
)

# Bearer tokens, long hex/base64 blobs and sk- style keys inside free text.
_SENSITIVE_VALUES = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"\b(sk|pk)_[A-Za-z0-9]{8,}\b"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
]


def redact_text(value: str) -> str:
    for pattern in _SENSITIVE_VALUES:
        value = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}" if m.groups() else REDACTED, value)
    return value


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (REDACTED if _SENSITIVE_KEYS.search(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            # Preserve the container type so %-formatting still works.
            if isinstance(record.args, dict):
                record.args = {key: redact(value) for key, value in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(redact(value) for value in record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "run_id", "path", "method", "status_code", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(redact(payload), default=str)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn keeps its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
