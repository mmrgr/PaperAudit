"""Smoke test for native PDF ingestion and page-aware evidence anchors."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperaudit.evidence import run_all  # noqa: E402
from paperaudit.ingest import read_pdf  # noqa: E402


def main() -> None:
    try:
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(f"缺少 smoke test 依赖 reportlab: {exc}")

    out = ROOT / "tests" / "_tmp" / "native-pdf-smoke.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(out), pagesize=(595, 842))
    pdf.setAuthor("Alice Example")
    pdf.setTitle("PaperAudit native PDF smoke")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(72, 780, "1 Introduction")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, 750, "The accuracy was 82% in the test set. See Figure 9-9.")
    pdf.drawString(72, 730, "Author: Alice Example")
    pdf.showPage()
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(72, 780, "References")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, 750, "[1] Example, A. (2024). A test reference. DOI: 10.1000/test.")
    pdf.save()

    doc = read_pdf(out)
    assert doc.metadata["format"] == "pdf"
    assert doc.metadata["pages"] == 2
    assert doc.blocks and all(block.page in {1, 2} for block in doc.blocks)
    assert all(block.bbox and len(block.bbox) == 4 for block in doc.blocks)
    assert doc.citations and doc.citations[0].index == 1
    findings = run_all(doc)
    assert any(f.issue_type.value == "privacy_risk" for f in findings)
    print({"pages": doc.metadata["pages"], "blocks": len(doc.blocks), "findings": len(findings), "status": "ok"})


if __name__ == "__main__":
    main()
