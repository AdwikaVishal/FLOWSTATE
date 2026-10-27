"""A deliberately small, declarative condition language.

Conditions are data, not code, so a learned workflow cannot smuggle executable
logic into the engine. Supported operators are intentionally limited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_MISSING = object()
# Public alias for callers that must distinguish "missing" from a JSON null.
MISSING = _MISSING

STATE_OPS = frozenset(
    {
        "eq",
        "ne",
        "in",
        "not_in",
        "exists",
        "truthy",
        "falsy",
        "gte",
        "lte",
        "gt",
        "lt",
        "expired",
        "not_expired",
    }
)


@dataclass(frozen=True)
class ConditionResult:
    ok: bool
    detail: str
    observed: Any = None


def resolve_path(data: Any, path: str) -> Any:
    """Resolve ``a.b.0.c`` against nested dicts and lists; return _MISSING if absent."""
    current = data
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return current


def path_exists(data: Any, path: str) -> bool:
    return resolve_path(data, path) is not _MISSING


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp from state, returning aware UTC or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def evaluate_condition(
    condition: dict[str, Any],
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> ConditionResult:
    path = str(condition.get("path", ""))
    op = str(condition.get("op", "eq"))
    expected = condition.get("value")
    observed = resolve_path(state, path)
    missing = observed is _MISSING
    shown = None if missing else observed

    if op not in STATE_OPS:
        return ConditionResult(False, f"unsupported operator {op!r}", shown)

    if op == "exists":
        return ConditionResult(not missing, f"{path} exists={not missing}", shown)

    if op in {"expired", "not_expired"}:
        if missing:
            return ConditionResult(op == "expired", f"{path} is missing", None)
        stamp = parse_timestamp(observed)
        if stamp is None:
            return ConditionResult(False, f"{path} is not a timestamp", shown)
        reference = now or datetime.now(UTC)
        is_expired = stamp <= reference
        ok = is_expired if op == "expired" else not is_expired
        return ConditionResult(
            ok,
            f"{path} expires {stamp.isoformat()}"
            + (" (expired)" if is_expired else " (still valid)"),
            shown,
        )

    if missing:
        return ConditionResult(False, f"{path} is missing", None)
    if op == "eq":
        return ConditionResult(observed == expected, f"{path} == {expected!r}", shown)
    if op == "ne":
        return ConditionResult(observed != expected, f"{path} != {expected!r}", shown)
    if op == "in":
        return ConditionResult(observed in (expected or []), f"{path} in {expected!r}", shown)
    if op == "not_in":
        return ConditionResult(
            observed not in (expected or []), f"{path} not in {expected!r}", shown
        )
    if op == "truthy":
        return ConditionResult(bool(observed), f"{path} is truthy", shown)
    if op == "falsy":
        return ConditionResult(not bool(observed), f"{path} is falsy", shown)

    left = _coerce_number(observed)
    right = _coerce_number(expected)
    if left is None or right is None:
        return ConditionResult(False, f"{path} is not numeric", shown)
    comparisons = {
        "gte": left >= right,
        "lte": left <= right,
        "gt": left > right,
        "lt": left < right,
    }
    return ConditionResult(comparisons[op], f"{path} {op} {right:g}", shown)


def evaluate_all(
    conditions: list[dict[str, Any]] | None,
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[ConditionResult]:
    return [evaluate_condition(item, state, now=now) for item in (conditions or [])]
