"""Smoke checks for registry discovery and explicit checklist aliases."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperaudit.checklist import list_checklists, load, recommend_checklists, resolve_checklist  # noqa: E402
from paperaudit.ingest import read_docx  # noqa: E402


def main() -> None:
    descriptors = list_checklists()
    assert descriptors and descriptors[0].id == "academic"
    assert resolve_checklist("generic") == Path(descriptors[0].path)
    assert len(load("academic").all_items()) >= 30
    assert resolve_checklist("prisma") is not None
    assert load("prisma").all_items()

    sample = ROOT / "tests" / "_tmp" / "seeded.docx"
    if sample.exists():
        ranked = recommend_checklists(read_docx(sample))
        assert ranked and ranked[0].id == "academic"
    print({"checklists": len(descriptors), "status": "ok"})


if __name__ == "__main__":
    main()
