"""Redaction for persisted run data.

Two rules apply together:

1. Any object key that looks sensitive is replaced, regardless of path.
2. A version or node may declare dot paths (``*`` allowed per segment) that are
   redacted even when the key name looks harmless.

Redaction is applied before data is written to runs, node states, checkpoints
and audit events. The engine keeps the raw values in memory for execution.
"""

from __future__ import annotations

import copy
import fnmatch
import re
from typing import Any

REDACTED = "[redacted]"
_SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|card|cvv|iban|account|ssn)",
    re.IGNORECASE,
)


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    parts = path.split(".") if path else []
    for pattern in patterns:
        expected = pattern.split(".")
        if len(expected) != len(parts):
            continue
        if all(
            fnmatch.fnmatchcase(part, token) for part, token in zip(parts, expected, strict=True)
        ):
            return True
    return False


def redact(value: Any, paths: list[str] | tuple[str, ...] | None = None, *, _path: str = "") -> Any:
    """Return a deep redacted copy. Input is never mutated."""
    patterns = tuple(str(item) for item in (paths or []))
    if _matches(_path, patterns):
        return REDACTED
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{_path}.{key}" if _path else str(key)
            if _SENSITIVE_KEY.search(str(key)):
                result[key] = REDACTED
            else:
                result[key] = redact(item, patterns, _path=child_path)
        return result
    if isinstance(value, (list, tuple)):
        return [
            redact(item, patterns, _path=f"{_path}.{index}" if _path else str(index))
            for index, item in enumerate(value)
        ]
    return copy.deepcopy(value)


def redact_state(
    state: dict[str, Any], paths: list[str] | tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Redact a runtime state mapping while keeping it JSON-serialisable."""
    return redact(state, paths)
