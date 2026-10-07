"""Focused checks for the P0 evidence and review-packet invariants."""

from __future__ import annotations

import tempfile
from pathlib import Path

from paperrevamper.evidence import DETECTORS, run_all
from paperrevamper.evidence.crossref import check as check_crossref
from paperrevamper.models import Block, BlockKind, DocumentIR, Finding, IssueType, Severity, Verdict
from paperrevamper.prepare import CHUNK_LIMIT, _template, _write_chunks
from paperrevamper.reporting import apply_gate, to_markdown
from paperrevamper.verify import _map_type


def main() -> int:
    # A bare caption is not evidence that the figure has a usable caption.
    doc = DocumentIR(
        "p",
        "paper.docx",
        "hash",
        blocks=[
            Block("p1", BlockKind.PARAGRAPH, "结果见图1。"),
            Block("p2", BlockKind.PARAGRAPH, "图1"),
        ],
    )
    assert check_crossref(doc)[0].issue_type is IssueType.CROSSREF_BROKEN

    # Source provenance in the report must reflect a mixed deterministic/LLM run.
    det = check_crossref(doc)[0]
    det.gate_passed = True
    llm = Finding("f", IssueType.CLARITY, Severity.MINOR, 0.8, ["p1"], "结果见图1。", "", source="llm")
    llm.gate_passed = True
    report = to_markdown(doc, [det, llm])
    assert "确定性检查与语言模型" in report

    # Detector failures are diagnostics and must stay unverifiable even when
    # the deterministic gate is called with allow_empty=True.
    original_detector = DETECTORS["structure"]
    DETECTORS["structure"] = lambda _doc: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        failed = run_all(doc, only=["structure"])
        assert failed and failed[0].gate_reason == "detector-error"
        assert failed[0].verdict is Verdict.UNVERIFIABLE
        apply_gate(doc, failed)
        assert failed[0].gate_passed is False
        assert failed[0].verdict is Verdict.UNVERIFIABLE
    finally:
        DETECTORS["structure"] = original_detector

    assert _map_type({"checklist_id": "C03"}) is IssueType.CITATION_MISSING
    assert _map_type({"checklist_id": "C04"}) is IssueType.CITATION_NUMBERING
    assert _map_type({"checklist_id": "X03"}) is IssueType.TERMINOLOGY_INCONSISTENT

    template = _template([type("Item", (), {"id": "A01", "q": "q", "sev": "major", "judge": "j", "scope": "all"})()])
    assert len(template["findings"]) == len(template["checklist"]) == 1

    with tempfile.TemporaryDirectory() as tmp:
        _write_chunks(
            DocumentIR("p", "paper.docx", "hash", [Block("p1", BlockKind.PARAGRAPH, "x" * 9000)]),
            Path(tmp),
        )
        chunks = list(Path(tmp).glob("*.md"))
        assert chunks and all(len(p.read_text(encoding="utf-8")) <= CHUNK_LIMIT for p in chunks)
        assert all("<untrusted_manuscript" in p.read_text(encoding="utf-8") for p in chunks)
    print("p0 correctness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
