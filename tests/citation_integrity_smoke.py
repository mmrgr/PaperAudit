"""Focused smoke checks for Citation Integrity v1.

Run with ``PYTHONPATH=src python tests/citation_integrity_smoke.py``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper.citations import (
    JsonEvidenceFetcher,
    JsonCache,
    audit_document,
    extract_identifiers,
    report,
    to_json,
)
from paperrevamper.citations.resolver import CrossrefResolver
from paperrevamper.models import Block, BlockKind, CitationEntry, CitationMark, DocumentIR


def main() -> int:
    identifiers = extract_identifiers(
        "[1] Smith, J. (2020). A title. DOI: 10.1234/ABC.1; PMID: 42; arXiv:2401.12345."
    )
    assert identifiers == {"doi": "10.1234/abc.1", "arxiv": "2401.12345", "pmid": "42"}

    # DOI-less author-year references may use a conservative Crossref
    # bibliographic search when online mode is explicitly enabled.
    crossref = CrossrefResolver()
    crossref._get_json = lambda url: {
        "message": {
            "items": [{
                "title": ["A title"],
                "author": [{"given": "J", "family": "Smith"}],
                "issued": {"date-parts": [[2020]]},
                "DOI": "10.1234/abc.1",
            }]
        }
    }
    crossref_record_doc = DocumentIR(
        "crossref-search", "paper.docx", "hash",
        citations=[CitationEntry(None, "smith|2020", "Smith, J. (2020). A title.", "r1")],
    )
    crossref_result = audit_document(crossref_record_doc, resolver=crossref)
    assert crossref_result["citations"][0]["metadata_status"] == "verified"
    assert crossref_result["citations"][0]["metadata"]["match_score"] >= 0.84
    crossref._get_json = lambda url: {"message": {"items": [{"title": ["Unrelated paper"], "issued": {"date-parts": [[2020]]}}]}}
    unresolved_search = audit_document(crossref_record_doc, resolver=crossref)
    assert unresolved_search["citations"][0]["metadata_status"] == "unresolved"
    crossref._get_json = lambda url: {"message": {"items": [{"title": ["A title"], "issued": {"date-parts": [[2021]]}}]}}
    wrong_year = audit_document(crossref_record_doc, resolver=crossref)
    assert wrong_year["citations"][0]["metadata_status"] == "unresolved"

    doc = DocumentIR(
        "paper",
        "paper.docx",
        "hash",
        citations=[
            CitationEntry(
                index=1,
                key="[1]",
                raw="[1] Smith, J. (2020). A title. doi:10.1234/abc.1",
                block_id="p_0002",
            ),
            CitationEntry(
                index=None,
                key="doe|2021",
                raw="Doe, A. (2021). Another title. PMID: 42",
                block_id="p_0003",
            ),
        ],
        citation_marks=[
            CitationMark("[1]", "1", "p_0001", (10, 13)),
            CitationMark("(Doe, 2021)", "doe|2021", "p_0004", (0, 11)),
        ],
    )

    calls: list[str] = []

    def resolver(record):
        calls.append(record.identifier)
        if record.doi:
            return {
                "status": "resolved",
                "metadata": {
                    "title": "A title",
                    "authors": ["Smith"],
                    "year": 2020,
                    "update-to": [{"type": "retraction"}],
                },
            }
        return {"status": "resolved", "metadata": {"title": "Different title", "year": 2022}}

    with tempfile.TemporaryDirectory() as tmp:
        cache = JsonCache(Path(tmp) / "citation-cache.json")
        result = audit_document(doc, resolver=resolver, cache=cache)
        assert result["summary"]["total"] == 2
        first, second = result["citations"]
        assert first["metadata_status"] == "verified"
        assert first["publication_status"] == "retracted"
        assert len(first["citation_sites"]) == 1
        assert first["citation_sites"][0]["claim"] == ""
        assert first["citation_sites"][0]["support_status"] == "uncertain"
        assert second["metadata_status"] == "mismatch"
        assert set(second["mismatch_fields"]) == {"title", "year"}

        # The second run is served by the local JSON cache.
        again = audit_document(doc, resolver=lambda _: (_ for _ in ()).throw(AssertionError("cache miss")), cache=cache)
        assert again["citations"][0]["metadata_status"] == "verified"
        assert len(calls) == 2

    # Numeric citation ranges link every referenced entry to the same site.
    range_doc = DocumentIR(
        "range",
        "range.docx",
        "hash",
        citations=[CitationEntry(i, f"[{i}]", f"[{i}] Entry {i} (2020).", f"p{i}") for i in (1, 2, 3)],
        citation_marks=[CitationMark("[1-3]", "1-3", "p0", (0, 5))],
    )
    range_result = audit_document(range_doc)
    assert all(len(item["citation_sites"]) == 1 for item in range_result["citations"])

    assert "Citation Integrity v1" in report(result)
    assert '"citations"' in to_json(result)

    # Resolver abstracts become evidence passages for the claim-support
    # heuristic; metadata titles alone must not count as evidence.
    support_doc = DocumentIR(
        "support",
        "support.docx",
        "hash",
        blocks=[Block("p_1", BlockKind.PARAGRAPH, "The treatment improved accuracy in the test set [1].")],
        citations=[CitationEntry(1, "[1]", "[1] Smith, J. (2020). A title. DOI: 10.1234/abc.1", "p_2")],
        citation_marks=[CitationMark("[1]", "1", "p_1", (49, 52))],
    )
    support_result = audit_document(
        support_doc,
        resolver=lambda _: {
            "status": "resolved",
            "metadata": {
                "title": "A title",
                "year": 2020,
                "abstract": "The treatment improved accuracy in the test set.",
            },
        },
    )
    assert support_result["citations"][0]["citation_sites"][0]["support_status"] == "supported"
    assert support_result["citations"][0]["citation_sites"][0]["evidence_confidence"] == 0.65
    assert support_result["citations"][0]["citation_sites"][0]["evidence_provenance"][0]["locator"] == "abstract"
    with tempfile.TemporaryDirectory() as tmp:
        evidence_path = Path(tmp) / "evidence.json"
        evidence_path.write_text(
            '{"doi:10.1234/abc.1": [{"text": "The treatment improved accuracy in the test set.", "source": "local-pack", "locator": "p. 3", "confidence": 0.91}]}',
            encoding="utf-8",
        )
        local_result = audit_document(
            support_doc,
            resolver=lambda _: {"status": "resolved", "metadata": {"title": "A title", "year": 2020}},
            evidence_fetcher=JsonEvidenceFetcher(evidence_path),
        )
        assert local_result["citations"][0]["citation_sites"][0]["support_status"] == "supported"
        assert local_result["citations"][0]["citation_sites"][0]["evidence_confidence"] == 0.91
        provenance = local_result["citations"][0]["citation_sites"][0]["evidence_provenance"]
        assert provenance == [{"source": "local-pack", "locator": "p. 3", "confidence": 0.91}]
        assert local_result["summary"]["evidence_sources"] == {"local-pack": 1}
        assert "local-pack@p. 3" in report(local_result)
        weak_path = Path(tmp) / "weak-evidence.json"
        weak_path.write_text(
            '{"doi:10.1234/abc.1": [{"text": "The treatment improved accuracy in the test set.", "source": "weak-pack", "confidence": 0.1}]}',
            encoding="utf-8",
        )
        weak_result = audit_document(
            support_doc,
            resolver=lambda _: {"status": "resolved", "metadata": {"title": "A title", "year": 2020}},
            evidence_fetcher=JsonEvidenceFetcher(weak_path),
        )
        weak_site = weak_result["citations"][0]["citation_sites"][0]
        assert weak_site["support_status"] == "uncertain"
        assert weak_site["evidence_confidence"] == 0.1
    print("citation integrity smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
