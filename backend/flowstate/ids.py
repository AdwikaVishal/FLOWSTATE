"""Identifier factory, injectable for deterministic tests."""

from __future__ import annotations

import uuid
from typing import Protocol


class IdFactory(Protocol):
    def new(self) -> str: ...


class UuidFactory:
    def new(self) -> str:
        return str(uuid.uuid4())


class SequentialIdFactory:
    def __init__(self, prefix: str = "00000000-0000-4000-8000-") -> None:
        self._prefix = prefix
        self._counter = 0

    def new(self) -> str:
        self._counter += 1
        return f"{self._prefix}{self._counter:012d}"
