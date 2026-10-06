"""Offline local text/TEI evidence retrieval smoke checks."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperaudit.citations import CitationRecord, LocalEvidenceFetcher, audit_document
from paperaudit.models import Block, BlockKind, CitationEntry, CitationMark, DocumentIR


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "evidence"
        root.mkdir()
        (root / "doi_10.1234_abcd.txt").write_text(
            "The treatment improved accuracy in the test set.\n\nAn unrelated passage.",
            encoding="utf-8",
        )
        (root / "doi_10.5678_tei.xml").write_text(
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<p>Survival improved after treatment.</p><p>Confidence intervals were reported.</p>'
            "</body></text></TEI>",
            encoding="utf-8",
        )
        fetcher = LocalEvidenceFetcher(root)
        doc = DocumentIR(
            "local-evidence",
            "paper.docx",
            "hash",
            blocks=[Block("p1", BlockKind.PARAGRAPH, "The treatment improved accuracy in the test set [1].")],
            citations=[CitationEntry(1, "[1]", "[1] Smith, J. (2020). A title. DOI: 10.1234/abcd", "p2")],
            citation_marks=[CitationMark("[1]", "1", "p1", (49, 52))],
        )
        result = audit_document(doc, resolver=lambda _: {"status": "resolved", "metadata": {"title": "A title"}}, evidence_fetcher=fetcher)
        site = result["citations"][0]["citation_sites"][0]
        assert site["support_status"] == "supported"
        assert site["evidence_provenance"]
        assert site["evidence_provenance"][0]["source"] == "local-text"
        assert site["evidence_provenance"][0]["locator"].endswith("#p1")
        assert result["summary"]["evidence_sources"] == {"local-text": 2}
        tei_record = CitationRecord(raw="", style="numeric", doi="10.5678/tei")
        tei_passages = fetcher.fetch_records(tei_record, {"claim": "survival improved"})
        assert tei_passages and tei_passages[0]["source"] == "local-tei"
        assert tei_passages[0]["locator"].endswith("#p1")
    print("local evidence smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
