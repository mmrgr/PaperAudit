"""Citation parsing and metadata integrity auditing."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .cache import cache_key
from .claim_linker import link_citation_claims
from .evidence_fetcher import fetch_evidence_records
from .models import CitationRecord, ParsedReference, metadata_from_payload
from .resolver import DeterministicResolver
from .support_judge import judge_support

_DOI_RE = re.compile(r"(?i)(?<![\w])(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)")
_ARXIV_RE = re.compile(
    r"(?i)(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)"
    r"((?:[a-z][\w.-]*/)?\d{4}\.\d{4,5}(?:v\d+)?|"
    r"(?:[a-z][\w.-]*/)?\d{7}(?:v\d+)?)"
)
_ARXIV_BARE_RE = re.compile(r"(?<![\w.])(\d{4}\.\d{4,5}(?:v\d+)?)(?![\w.])")
_PMID_RE = re.compile(r"(?i)(?:pmid\s*[:#]?\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{1,10})")
_YEAR_RE = re.compile(r"(?<!\d)((?:18|19|20|21)\d{2})(?!\d)")
_BIB_NUM_RE = re.compile(r"^\s*\[(\d+)\]\s*")


def extract_identifiers(raw: str) -> dict[str, str]:
    """Extract DOI, arXiv and PMID identifiers from a reference string."""

    text = str(raw or "")
    doi_match = _DOI_RE.search(text)
    doi = _clean_identifier(doi_match.group(1), "doi") if doi_match else ""
    arxiv_match = _ARXIV_RE.search(text)
    if not arxiv_match:
        # Bare IDs are accepted only when they look like modern arXiv IDs;
        # four-digit years alone therefore cannot become false identifiers.
        arxiv_match = _ARXIV_BARE_RE.search(text)
    arxiv = _clean_identifier(arxiv_match.group(1), "arxiv") if arxiv_match else ""
    pmid_match = _PMID_RE.search(text)
    pmid = pmid_match.group(1) if pmid_match else ""
    return {"doi": doi, "arxiv": arxiv, "pmid": pmid}


def _clean_identifier(value: str, kind: str) -> str:
    value = value.strip().rstrip(".,;:)]}>")
    if kind == "doi":
        value = value.casefold()
    return value


def detect_style(raw: str, *, index: int | None = None) -> str:
    """Guess a common bibliography style using conservative lexical cues."""

    text = str(raw or "").strip()
    if index is not None or _BIB_NUM_RE.match(text):
        # Numeric entries may be IEEE, Vancouver or GB/T.  The distinction is
        # not reliable from one line, so expose the useful umbrella label.
        return "numeric"
    if re.search(r"[一-鿿]", text):
        return "gbt7714"
    if re.search(r"\bet\s+al\.?\b|\&", text, re.I) and re.search(r"\(\s*\d{4}[a-z]?\s*\)", text, re.I):
        return "apa"
    if re.search(r"\(\s*\d{4}[a-z]?\s*\)", text, re.I):
        return "harvard"
    if re.search(r"\b(?:19|20|21)\d{2}\b", text):
        return "author_year"
    return "unknown"


def parse_reference(raw: str, *, index: int | None = None) -> ParsedReference:
    """Parse title, first author and year without asserting correctness.

    Reference styles vary too much for a strict grammar.  This parser keeps a
    deliberately conservative approximation that is only used for metadata
    comparison; the original raw string is always retained in the record.
    """

    text = str(raw or "").strip()
    text = _BIB_NUM_RE.sub("", text, count=1).strip()
    years = list(_YEAR_RE.finditer(text))
    year = int(years[0].group(1)) if years else None
    authors: tuple[str, ...] = ()
    title = ""

    if years:
        match = years[0]
        author_text = text[: match.start()].strip(" .,:;()[]")
        if author_text:
            authors = tuple(_split_authors(author_text))
        # Remove the year and surrounding punctuation; the next sentence-like
        # segment is usually the title in APA/Harvard/GB/T references.
        tail = text[match.end() :].lstrip(" .,:;()[]")
        title = _title_segment(tail)
    else:
        # Numeric references occasionally omit a year.  Quoted text is a good
        # title cue; otherwise retain the first sentence-like segment.
        quoted = re.search(r"[\"“](.+?)[\"”]", text)
        title = quoted.group(1).strip() if quoted else _title_segment(text)
        prefix = text[: text.find(title)] if title and title in text else ""
        authors = tuple(_split_authors(prefix.strip(" .,:;()[]"))) if prefix else ()

    return ParsedReference(title=title, authors=authors, year=year)


def _split_authors(text: str) -> list[str]:
    text = re.sub(r"\bet\s+al\.?\b", "", text, flags=re.I).strip()
    if not text:
        return []
    # Stop at a likely title delimiter.  Keep names with initials and Chinese
    # names intact, while supporting both comma and semicolon author lists.
    pieces = re.split(r"\s*;\s*|\s+and\s+|\s*&\s*|\s+等\s*|(?<=\.)\s+(?=[A-Z一-鿿])", text, flags=re.I)
    return [p.strip(" .,") for p in pieces if p.strip(" .,")]


def _title_segment(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    quoted = re.match(r"[\"“](.+?)[\"”](?:\.|$)", text)
    if quoted:
        return quoted.group(1).strip()
    # A DOI/URL is not a useful title.  Prefer the text before the first
    # sentence delimiter, while retaining titles containing abbreviations.
    parts = re.split(r"\s*\.\s+(?=[A-Z一-鿿0-9])|\s*[。！？]\s*", text, maxsplit=1)
    candidate = parts[0].strip(" .,:;[]")
    candidate = re.sub(r"\s+(?:doi|https?://)\s*[:]?\s*\S+$", "", candidate, flags=re.I).strip()
    return candidate


def citation_sites_for(entry: Any, marks: list[Any], doc: Any = None) -> list[dict[str, Any]]:
    """Return citation occurrences matching one ``CitationEntry``."""

    if doc is not None:
        return link_citation_claims(doc, entry, marks)

    key = str(getattr(entry, "key", "") or "")
    index = getattr(entry, "index", None)
    sites: list[dict[str, Any]] = []
    for mark in marks or []:
        mark_key = str(getattr(mark, "key", "") or "")
        matched = _mark_matches(mark_key, key, index)
        if not matched:
            continue
        char_range = getattr(mark, "char_range", (0, 0))
        sites.append(
            {
                "block_id": str(getattr(mark, "block_id", "") or ""),
                "location": str(getattr(mark, "block_id", "") or ""),
                "char_range": list(char_range) if isinstance(char_range, (tuple, list)) else [],
                "raw": str(getattr(mark, "raw", "") or ""),
            }
        )
    return sites


def _mark_matches(mark_key: str, entry_key: str, index: int | None) -> bool:
    if index is not None:
        nums: list[int] = []
        for part in re.split(r"[,;]", mark_key):
            part = part.strip()
            range_match = re.fullmatch(r"(\d+)\s*[\-–—]\s*(\d+)", part)
            if range_match:
                start, end = map(int, range_match.groups())
                if start > end:
                    start, end = end, start
                nums.extend(range(start, end + 1))
            else:
                nums.extend(int(n) for n in re.findall(r"\d+", part))
        return index in nums
    return bool(entry_key and mark_key.casefold() == entry_key.casefold())


def build_records(doc: Any, *, resolver: Any = None, cache: Any = None) -> list[CitationRecord]:
    """Build and resolve records from a compatible ``DocumentIR`` object."""

    selected_resolver = resolver if resolver is not None else DeterministicResolver()
    records: list[CitationRecord] = []
    entries = list(getattr(doc, "citations", []) or [])
    marks = list(getattr(doc, "citation_marks", []) or [])
    for entry in entries:
        raw = str(getattr(entry, "raw", "") or "")
        index = getattr(entry, "index", None)
        parsed = parse_reference(raw, index=index)
        identifiers = extract_identifiers(raw)
        record = CitationRecord(
            raw=raw,
            style=detect_style(raw, index=index),
            key=str(getattr(entry, "key", "") or ""),
            index=index,
            block_id=str(getattr(entry, "block_id", "") or ""),
            title=parsed.title,
            authors=list(parsed.authors),
            year=parsed.year,
            citation_sites=citation_sites_for(entry, marks, doc),
            **identifiers,
        )
        _resolve_record(record, selected_resolver, cache)
        records.append(record)
    return records


def _resolve_record(record: CitationRecord, resolver: Any, cache: Any) -> None:
    identifier = record.identifier
    key = cache_key(identifier) if identifier else ""
    payload: Any = None
    if cache is not None and key:
        payload = _cache_get(cache, key, identifier)
    if payload is None:
        try:
            payload = _call_resolver(resolver, record)
        except Exception:
            # Resolver plugins are untrusted IO boundaries.  A failed plugin
            # must produce an unresolved record rather than aborting an audit.
            payload = {"status": "unresolved", "source": "resolver_error", "metadata": {}}
        if cache is not None and key:
            _cache_set(cache, key, payload)
    metadata = metadata_from_payload(payload)
    response_status = _payload_status(payload)
    if not metadata:
        record.metadata_status = "unresolved"
        record.publication_status = "unknown"
        return
    record.metadata = metadata
    fields = compare_metadata(record, metadata)
    record.mismatch_fields = fields
    record.metadata_mismatch = bool(fields)
    record.metadata_status = "mismatch" if fields else ("unresolved" if response_status == "unresolved" else "verified")
    record.publication_status = publication_status(metadata) if record.metadata_status != "unresolved" else "unknown"


def _call_resolver(resolver: Any, record: CitationRecord) -> Any:
    if isinstance(resolver, Mapping):
        return resolver.get(record.identifier) or resolver.get(record.doi) or resolver.get(record.arxiv) or resolver.get(record.pmid)
    method = getattr(resolver, "resolve", None)
    if callable(method):
        try:
            return method(record)
        except (TypeError, AttributeError):
            # A tiny integration often exposes ``resolve(identifier)`` rather
            # than accepting the full record.  Supporting that form keeps the
            # resolver boundary pleasant without imposing an ABC.
            return method(record.identifier)
    if callable(resolver):
        try:
            return resolver(record)
        except (TypeError, AttributeError):
            return resolver(record.identifier)
    return None


def _payload_status(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return "unresolved"
    status = str(payload.get("status", payload.get("metadata_status", "resolved"))).casefold()
    return "unresolved" if status in {"unresolved", "error", "failed", "not_found", "unknown"} else status


def _cache_get(cache: Any, key: str, identifier: str) -> Any:
    try:
        if isinstance(cache, Mapping):
            return cache.get(key, cache.get(identifier))
        getter = getattr(cache, "get", None)
        if callable(getter):
            value = getter(key)
            return value if value is not None else getter(identifier)
    except Exception:
        return None
    return None


def _cache_set(cache: Any, key: str, value: Any) -> None:
    try:
        if isinstance(cache, dict):
            cache[key] = value
            return
        setter = getattr(cache, "set", None)
        if callable(setter):
            setter(key, value)
    except Exception:
        return


def compare_metadata(record: CitationRecord, metadata: Mapping[str, Any]) -> list[str]:
    """Return fields where local reference and resolved metadata disagree."""

    mismatches: list[str] = []
    ref_title = _norm_title(record.title)
    meta_title = _norm_title(_first(metadata, "title", "name"))
    if ref_title and meta_title and _title_similarity(ref_title, meta_title) < 0.72:
        mismatches.append("title")

    ref_year = record.year
    meta_year = _as_year(_first(metadata, "year", "publication_year", "issued"))
    if ref_year and meta_year and ref_year != meta_year:
        mismatches.append("year")

    ref_doi = _norm_identifier(record.doi)
    meta_doi = _norm_identifier(_first(metadata, "doi", "DOI"))
    if ref_doi and meta_doi and ref_doi != meta_doi:
        mismatches.append("doi")

    ref_author = _author_token(record.authors[0] if record.authors else "")
    meta_authors = metadata.get("authors", metadata.get("author", []))
    if isinstance(meta_authors, str):
        meta_authors = [meta_authors]
    if isinstance(meta_authors, Mapping):
        meta_authors = [meta_authors]
    if ref_author and isinstance(meta_authors, (list, tuple)) and meta_authors:
        first = meta_authors[0]
        if isinstance(first, Mapping):
            first = " ".join(str(first.get(k, "")) for k in ("family", "last", "name") if first.get(k))
        if _author_token(str(first)) and _author_token(str(first)) != ref_author:
            mismatches.append("authors")
    return mismatches


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            if key == "issued" and isinstance(value, Mapping):
                parts = value.get("date-parts", [])
                return parts[0][0] if parts and parts[0] else None
            return value
    return None


def _as_year(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = _YEAR_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def _norm_identifier(value: Any) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", str(value or "").strip(), flags=re.I).rstrip(".,;)").casefold()


def _norm_title(value: Any) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(value or "").casefold()).strip()


def _title_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(a=left, b=right).ratio()


def _author_token(value: str) -> str:
    text = str(value or "").casefold().strip()
    if "," in text or "，" in text:
        # Bibliographies commonly use ``Family, Given`` while resolver APIs
        # often return ``Given Family``.
        text = re.split(r"[,，]", text, maxsplit=1)[0]
    words = re.findall(r"[a-z\u4e00-\u9fff]+", text)
    return words[-1] if words else ""


def publication_status(metadata: Mapping[str, Any]) -> str:
    """Classify clear publication updates without guessing from absence."""

    parts: list[str] = []
    for key in ("status", "publication_status", "type", "subtype", "title", "update-to", "updates", "publicationTypes"):
        value = metadata.get(key, "")
        if isinstance(value, (list, tuple, dict)):
            value = json.dumps(value, ensure_ascii=False)
        parts.append(str(value).casefold())
    text = " ".join(parts)
    if re.search(r"\bretract(?:ed|ion)?\b|withdrawn|retracted", text):
        return "retracted"
    if re.search(r"expression\s+of\s+concern|\bconcern\b", text):
        return "concern"
    if re.search(r"correct(?:ed|ion)|erratum|corrigendum", text):
        return "corrected"
    return "normal"


def audit_document(doc_or_path: Any, resolver: Any = None, cache: Any = None, evidence_fetcher: Any = None) -> dict[str, Any]:
    """Audit citations in a ``DocumentIR`` or a DOCX path.

    The function is intentionally side-effect free unless an explicit cache is
    supplied.  Offline deterministic resolution is the default; callers must
    pass ``CrossrefResolver`` or another resolver to enable network lookups.
    """

    doc = _coerce_document(doc_or_path)
    records = build_records(doc, resolver=resolver, cache=cache)
    support_counts: dict[str, int] = {}
    evidence_sources: dict[str, int] = {}
    for record in records:
        for site in record.citation_sites:
            evidence_records = fetch_evidence_records(record, evidence_fetcher, site)
            evidence = [item["text"] for item in evidence_records]
            judgement = judge_support(
                site.get("claim", ""),
                evidence,
                evidence_records=evidence_records,
            )
            site.update({
                "support_status": judgement["status"],
                "support_confidence": judgement["confidence"],
                "evidence_confidence": judgement.get("evidence_confidence", 0.0),
                "support_method": judgement["method"],
                "support_calibration": judgement.get("calibration", "none"),
                "evidence_passages": judgement["evidence_passages"],
                "evidence_provenance": [
                    {key: value for key, value in item.items() if key != "text"}
                    for item in evidence_records
                ],
            })
            for item in evidence_records:
                source = str(item.get("source") or "unknown")
                evidence_sources[source] = evidence_sources.get(source, 0) + 1
            status = str(judgement["status"])
            support_counts[status] = support_counts.get(status, 0) + 1
    counts: dict[str, int] = {}
    for record in records:
        counts[record.metadata_status] = counts.get(record.metadata_status, 0) + 1
    return {
        "version": "citation-integrity-v1",
        "status": "ok",
        "document": {
            "doc_id": str(getattr(doc, "doc_id", "")),
            "source_path": str(getattr(doc, "source_path", "")),
            "source_hash": str(getattr(doc, "source_hash", "")),
            "citation_entries": len(getattr(doc, "citations", []) or []),
            "citation_marks": len(getattr(doc, "citation_marks", []) or []),
        },
        "summary": {
            "total": len(records),
            "metadata_status": counts,
            "uncited": sum(1 for record in records if not record.citation_sites),
            "publication_status": _status_counts(records),
            "support_status": support_counts,
            "evidence_sources": evidence_sources,
        },
        "citations": [record.to_dict() for record in records],
    }


def _coerce_document(value: Any) -> Any:
    if hasattr(value, "citations") and hasattr(value, "citation_marks"):
        return value
    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.casefold() not in {".docx", ".pdf", ".xml"}:
        raise ValueError("Citation Integrity currently accepts DocumentIR, .docx, .pdf, or GROBID .xml paths")
    from paperrevamper.ingest import read_document

    return read_document(path)


def _status_counts(records: list[CitationRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.publication_status] = counts.get(record.publication_status, 0) + 1
    return counts
