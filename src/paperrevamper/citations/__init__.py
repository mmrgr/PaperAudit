"""Citation Integrity v1 public API.

The package has no network side effects on import.  Use
``CrossrefResolver``/``SemanticScholarResolver`` explicitly when online
metadata lookup is desired.
"""

from .cache import CitationCache, JsonCache, LocalJsonCache, cache_key
from .claim_linker import link_citation_claims, sentence_claim
from .evidence_fetcher import (
    JsonEvidenceFetcher,
    LocalEvidenceFetcher,
    fetch_evidence,
    fetch_evidence_records,
    metadata_evidence,
    metadata_passages,
)
from .models import CitationRecord, ParsedReference
from .parser import (
    audit_document,
    build_records,
    citation_sites_for,
    compare_metadata,
    detect_style,
    extract_identifiers,
    parse_reference,
    publication_status,
)
from .reporting import report, to_json, to_markdown
from .resolver import CrossrefResolver, DeterministicResolver, SemanticScholarResolver
from .support_judge import judge_support

__all__ = [
    "CitationCache",
    "CitationRecord",
    "CrossrefResolver",
    "DeterministicResolver",
    "JsonCache",
    "JsonEvidenceFetcher",
    "LocalEvidenceFetcher",
    "LocalJsonCache",
    "ParsedReference",
    "SemanticScholarResolver",
    "audit_document",
    "build_records",
    "cache_key",
    "citation_sites_for",
    "fetch_evidence",
    "fetch_evidence_records",
    "judge_support",
    "link_citation_claims",
    "metadata_passages",
    "metadata_evidence",
    "compare_metadata",
    "detect_style",
    "extract_identifiers",
    "parse_reference",
    "publication_status",
    "sentence_claim",
    "report",
    "to_json",
    "to_markdown",
]
