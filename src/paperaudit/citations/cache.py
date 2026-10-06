"""Small atomic JSON cache for citation metadata.

No database or third-party cache dependency is needed.  The cache is
intentionally conservative: corrupt files are ignored and writes are made
through a sibling temporary file followed by ``replace``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def cache_key(identifier: str) -> str:
    return hashlib.sha256(identifier.strip().casefold().encode("utf-8")).hexdigest()


class JsonCache:
    """A JSON mapping persisted at ``path``.

    ``get`` and ``set`` are deliberately dict-like so callers can inject a
    tiny in-memory fake during tests.  A missing/corrupt cache behaves as an
    empty cache.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            self._data = {}
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        data = self._load()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def clear(self) -> None:
        self._data = {}
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# Friendly aliases used by integrations which describe the implementation as
# a local cache rather than a generic JSON mapping.
LocalJsonCache = JsonCache
CitationCache = JsonCache

