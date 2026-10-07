"""Locate PaperRevamper resources in source and frozen (PyInstaller) runs."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Return the first existing bundled/project resource path."""
    candidates: list[Path] = []
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS))
    # ``setuptools.data-files`` installs the built-in checklist packs below
    # the active environment prefix.  This keeps wheel installs functional
    # even though the source-tree ``checklists/`` directory is absent.
    candidates.append(Path(sys.prefix))
    candidates.append(Path(sys.executable).resolve().parent)
    # Source tree: src/paperrevamper/resources.py -> project root is parents[2].
    candidates.append(Path(__file__).resolve().parents[2])
    for root in candidates:
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return candidate
    return candidates[0].joinpath(*parts)
