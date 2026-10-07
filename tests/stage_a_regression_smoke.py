"""Focused Stage A regressions for identity, evidence, and endpoint policy."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperaudit.llm import _validate_endpoint  # noqa: E402
from paperaudit.models import Block, BlockKind, DocumentIR, Finding, IssueType, Severity  # noqa: E402
from paperaudit.panel_state import append_decision, build_state  # noqa: E402
from paperaudit.reporting import gate_finding  # noqa: E402
from paperaudit.runner import _MAX_ARTIFACT_BYTES, _read_artifacts  # noqa: E402


def main() -> None:
    first = Finding(
        id="F001",
        issue_type=IssueType.CLARITY,
        severity=Severity.MINOR,
        confidence=0.8,
        block_ids=["p_1"],
        verbatim_quote="A clear sentence.",
        checklist_id="academic/clarity",
    )
    second = Finding(
        id="F999",
        issue_type=IssueType.CLARITY,
        severity=Severity.MINOR,
        confidence=0.8,
        block_ids=["p_1"],
        verbatim_quote="A  clear   sentence.",
        checklist_id="academic/clarity",
    )
    assert first.uid == second.uid and first.uid.startswith("fd_")

    document = DocumentIR(
        "smoke",
        "paper.docx",
        "hash-new",
        blocks=[
            Block("p_1", BlockKind.PARAGRAPH, "sample size was 10"),
            Block("p_2", BlockKind.PARAGRAPH, "A clear sentence."),
        ],
    )
    mixed_blocks = Finding(
        id="F002",
        issue_type=IssueType.CLARITY,
        severity=Severity.MINOR,
        confidence=0.8,
        block_ids=["p_1", "missing"],
        verbatim_quote="sample size was 10",
    )
    assert not gate_finding(document, mixed_blocks).gate_passed
    fabricated = Finding(
        id="F003",
        issue_type=IssueType.NUMERIC_INCONSISTENCY,
        severity=Severity.MAJOR,
        confidence=0.8,
        block_ids=["p_1"],
        verbatim_quote="sample size was10",
    )
    assert not gate_finding(document, fabricated).gate_passed

    with tempfile.TemporaryDirectory(prefix="paperaudit-stage-a-") as temp:
        run = Path(temp)
        (run / "manifest.json").write_text(json.dumps({"hash": "hash-new"}), encoding="utf-8")
        (run / "findings.json").write_text(
            json.dumps({"confirmed": [first.to_dict()], "rejected": []}), encoding="utf-8"
        )
        append_decision(run, first.id, "accept", "current", "hash-new", finding_uid=first.uid)
        append_decision(run, first.id, "reject", "stale", "hash-old", finding_uid=first.uid)
        state = build_state(run)
        assert state["decisions"][first.uid]["decision"] == "accept"
        oversized = run / "oversized.md"
        oversized.write_text("x" * (_MAX_ARTIFACT_BYTES + 100), encoding="utf-8")
        context_meta: dict[str, object] = {}
        _read_artifacts(run, ["oversized.md"], metadata=context_meta)
        assert context_meta["truncated"] is True
        assert "oversized.md" in context_meta["truncated_files"]

    try:
        _validate_endpoint("http://169.254.169.254")
    except ValueError:
        pass
    else:
        raise AssertionError("link-local endpoint must be rejected")
    _validate_endpoint("http://127.0.0.1:8000")
    print("stage A regression smoke checks passed")


if __name__ == "__main__":
    main()
