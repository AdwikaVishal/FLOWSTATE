"""Content-addressed evidence storage."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from flowstate.clock import Clock
from flowstate.db.models import EvidenceRecord
from flowstate.domain.enums import EvidenceKind
from flowstate.ids import IdFactory
from flowstate.services.audit import canonical_json


class EvidenceStore:
    def __init__(self, root: Path, clock: Clock, ids: IdFactory) -> None:
        self._root = root
        self._clock = clock
        self._ids = ids

    def write_json(
        self,
        *,
        run_id: str,
        kind: EvidenceKind,
        payload: dict[str, Any],
        event_id: str | None = None,
        captured_at: datetime | None = None,
    ) -> EvidenceRecord:
        body = canonical_json(payload).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        directory = self._root / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.json"
        if not path.exists():
            path.write_bytes(body)
        return EvidenceRecord(
            id=self._ids.new(),
            run_id=run_id,
            event_id=event_id,
            kind=str(kind),
            uri=str(path),
            sha256=digest,
            mime_type="application/json",
            meta_json={"bytes": len(body), "encoding": "canonical-json"},
            captured_at=captured_at or self._clock.now(),
        )

    def write_bytes(
        self,
        *,
        owner_id: str,
        kind: EvidenceKind,
        content: bytes,
        mime_type: str,
        extension: str,
        event_id: str | None = None,
    ) -> EvidenceRecord:
        """Store opaque bytes (a screenshot) content-addressed.

        ``owner_id`` is the run or capture id; the column is intentionally not a
        foreign key so the same evidence mechanism serves learning sessions.
        """
        digest = hashlib.sha256(content).hexdigest()
        directory = self._root / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.{extension}"
        if not path.exists():
            path.write_bytes(content)
        return EvidenceRecord(
            id=self._ids.new(),
            run_id=owner_id,
            event_id=event_id,
            kind=str(kind),
            uri=str(path),
            sha256=digest,
            mime_type=mime_type,
            meta_json={"bytes": len(content), "encoding": "binary", "extension": extension},
            captured_at=self._clock.now(),
        )

    @staticmethod
    def verify(record: EvidenceRecord) -> bool:
        path = Path(record.uri)
        if not path.exists():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == record.sha256

    @staticmethod
    def read_json(record: EvidenceRecord) -> dict[str, Any]:
        return json.loads(Path(record.uri).read_text(encoding="utf-8"))
