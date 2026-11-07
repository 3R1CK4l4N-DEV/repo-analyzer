"""In-memory parser results for repeated analysis of a project.

Cached results are returned as shared objects: copying 3,000 parsed classes cost more than the
parsing it saved. Plugins only re-apply idempotent classification on them, and analyses produced
by an incremental analyzer must be treated as read-only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

from devanalyzer.core.models import SourceFile

T = TypeVar("T")


class ParseCache(Generic[T]):
    def __init__(self):
        self._entries: dict[str, tuple[tuple, T]] = {}

    def clear(self) -> None:
        self._entries.clear()

    def parse(self, files: list[SourceFile], parser: Callable[[SourceFile], T], context: Hashable = None) -> list[T]:
        entries: dict[str, tuple[tuple, T]] = {}
        parsed: list[T] = []
        for source in files:
            fingerprint = (source.rel_path, source.language, source.extension, source.size, hashlib.sha256(source.content.encode("utf-8")).digest(), context)
            previous = self._entries.get(source.path)
            result = previous[1] if previous and previous[0] == fingerprint else parser(source)
            entries[source.path] = (fingerprint, result)
            parsed.append(result)
        self._entries = entries  # discard removed/renamed files
        return parsed
