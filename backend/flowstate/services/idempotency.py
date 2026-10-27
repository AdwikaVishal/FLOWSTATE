"""Idempotent mutation responses for adapter retries.

The adapter may retry a consequential call after a transport failure. A caller
supplied ``Idempotency-Key`` makes the backend return the first persisted result
instead of creating a second run or recovery attempt. A reused key with a
different request is rejected rather than silently replayed.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from flowstate.clock import Clock
from flowstate.db.models import IdempotencyRecord
from flowstate.errors import ConflictError, ValidationError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services.audit import canonical_json

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
_TTL = timedelta(hours=24)


def validate_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not _KEY.match(value.strip()):
        raise ValidationError(
            "Idempotency-Key must be 8-160 characters of letters, digits, '.', '_', ':' or '-'.",
            code="INVALID_IDEMPOTENCY_KEY",
        )
    return value.strip()


def request_hash(scope: str, actor_id: str, payload: Mapping[str, Any]) -> str:
    material = {"scope": scope, "actor": actor_id, "payload": dict(payload)}
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def lookup(
    uow: UnitOfWork,
    *,
    scope: str,
    key: str,
    fingerprint: str,
    now: Any,
) -> dict[str, Any] | None:
    record = uow.idempotency.first(scope=scope, key=key)
    if record is None:
        return None
    if record.expires_at and record.expires_at <= now:
        uow.idempotency.delete(record)
        return None
    if not hmac.compare_digest(record.request_hash, fingerprint):
        raise ConflictError(
            "This Idempotency-Key was already used for a different request.",
            code="IDEMPOTENCY_CONFLICT",
            details={"scope": scope, "key": key},
        )
    return dict(record.response_json or {})


def remember(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    *,
    scope: str,
    key: str,
    fingerprint: str,
    response: Mapping[str, Any],
    status_code: int,
) -> None:
    now = clock.now()
    uow.idempotency.add(
        IdempotencyRecord(
            id=ids.new(),
            scope=scope,
            key=key,
            request_hash=fingerprint,
            response_json=dict(response),
            status_code=status_code,
            created_at=now,
            expires_at=now + _TTL,
        )
    )
