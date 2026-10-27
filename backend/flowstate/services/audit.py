"""Hash-linked audit trail.

Every accepted state transition appends exactly one event. The chain is
recomputable from the payloads alone, which is what makes the audit tab
meaningful rather than decorative.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from flowstate.clock import Clock
from flowstate.db.models import ExecutionEvent
from flowstate.domain.enums import ActorKind, EventType
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork

logger = logging.getLogger(__name__)


def canonical_json(payload: dict[str, Any] | list[Any] | None) -> str:
    return json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, event_type: str, seq: int, payload: dict[str, Any]) -> str:
    material = f"{prev_hash}|{event_type}|{seq}|{canonical_json(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    event_count: int
    head_hash: str
    broken_at_seq: int | None = None
    reason: str | None = None


class AuditWriter:
    def __init__(self, uow: UnitOfWork, clock: Clock, ids: IdFactory) -> None:
        self._uow = uow
        self._clock = clock
        self._ids = ids

    def append(
        self,
        run_id: str,
        event_type: EventType | str,
        *,
        actor: ActorKind = ActorKind.SYSTEM,
        actor_id: str = "system",
        action_ordinal: int | None = None,
        node_key: str | None = None,
        payload: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> ExecutionEvent:
        last = self._uow.events.last(run_id)
        seq = 0 if last is None else last.seq + 1
        prev_hash = "" if last is None else last.hash
        event_type_str = str(event_type)
        body = payload or {}
        event = ExecutionEvent(
            id=self._ids.new(),
            run_id=run_id,
            seq=seq,
            type=event_type_str,
            actor=str(actor),
            actor_id=actor_id,
            action_ordinal=action_ordinal,
            node_key=node_key,
            payload=body,
            prev_hash=prev_hash,
            hash=compute_hash(prev_hash, event_type_str, seq, body),
            recorded_at=at or self._clock.now(),
        )
        self._uow.events.append([event])
        return event


def verify_chain(events: Sequence[ExecutionEvent]) -> ChainVerification:
    if not events:
        return ChainVerification(valid=True, event_count=0, head_hash="")

    prev_hash = ""
    for expected_seq, event in enumerate(events):
        if event.seq != expected_seq:
            return ChainVerification(
                valid=False,
                event_count=len(events),
                head_hash=prev_hash,
                broken_at_seq=event.seq,
                reason=f"expected seq {expected_seq}, found {event.seq}",
            )
        if event.prev_hash != prev_hash:
            return ChainVerification(
                valid=False,
                event_count=len(events),
                head_hash=prev_hash,
                broken_at_seq=event.seq,
                reason="prev_hash does not match the previous event",
            )
        recomputed = compute_hash(event.prev_hash, event.type, event.seq, event.payload or {})
        if recomputed != event.hash:
            return ChainVerification(
                valid=False,
                event_count=len(events),
                head_hash=prev_hash,
                broken_at_seq=event.seq,
                reason="payload hash mismatch (event was modified)",
            )
        prev_hash = event.hash

    return ChainVerification(valid=True, event_count=len(events), head_hash=prev_hash)
