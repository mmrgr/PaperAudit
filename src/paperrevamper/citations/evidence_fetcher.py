"""Evidence passage adapters for citation support judging.

The default adapter is deliberately local.  It can use an abstract or
description returned by an explicitly enabled metadata resolver, but it never
downloads a paper or treats a title alone as evidence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def metadata_passages(record: Any) -> list[str]:
    return [item["text"] for item in metadata_evidence(record)]


def metadata_evidence(record: Any) -> list[dict[str, Any]]:
    """Return resolver metadata as explicitly labelled evidence records.

    An abstract is useful triage evidence, but it is not silently presented as
    a full-text passage.  The locator therefore remains the metadata field
    name and the source is marked as resolver metadata.
    """

    metadata = getattr(record, "metadata", {}) or {}
    if not isinstance(metadata, Mapping):
        return []
    source = str(metadata.get("source") or "resolver-metadata")
    records: list[dict[str, Any]] = []
    confidence_by_field = {
        "abstract": 0.65,
        "description": 0.60,
        "summary": 0.55,
        "snippet": 0.45,
    }
    for key in ("abstract", "description", "summary", "snippet"):
        for item in _normalize_evidence(metadata.get(key), source=source, locator=key):
            # Resolver metadata is useful triage evidence, but it is not a
            # full-text excerpt.  Keep an explicit lower prior while allowing
            # a provider-supplied confidence to override it.
            item.setdefault("confidence", confidence_by_field[key])
            records.append(item)
    return records


def fetch_evidence(record: Any, fetcher: Any = None, site: Mapping[str, Any] | None = None) -> list[str]:
    """Call an optional evidence fetcher safely, returning text only.

    This compatibility wrapper preserves the v1 API.  New callers should use
    :func:`fetch_evidence_records` when source and locator provenance matters.
    """

    return [item["text"] for item in fetch_evidence_records(record, fetcher, site)]


def fetch_evidence_records(
    record: Any,
    fetcher: Any = None,
    site: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch normalized evidence records without discarding provenance."""

    if fetcher is None:
        return metadata_evidence(record)
    try:
        fetch_records = getattr(fetcher, "fetch_records", None)
        if callable(fetch_records):
            try:
                value = fetch_records(record, site)
            except TypeError:
                value = fetch_records(record)
        elif callable(fetcher):
            try:
                value = fetcher(record, site)
            except TypeError:
                value = fetcher(record)
        else:
            try:
                value = fetcher.fetch(record, site)
            except TypeError:
                value = fetcher.fetch(record)
    except Exception:
        return []
    return _normalize_evidence(value, source="custom-fetcher")


class JsonEvidenceFetcher:
    """Read author-supplied evidence passages from a local JSON pack.

    Accepted forms are either ``{"doi:...": ["passage"]}`` or a list of
    objects with ``identifier``/``key`` and ``passages`` fields.  Passage
    objects may add ``source``, ``locator`` and ``confidence``.  The source
    remains explicit in the report; this class never treats a title or an
    unresolved key as evidence.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        if isinstance(payload, dict):
            rows = payload.get("evidence", payload)
            if isinstance(rows, dict):
                for key, passages in rows.items():
                    normalized = _normalize_evidence(passages, source="local-json")
                    if normalized:
                        for variant in _identifier_variants(key):
                            result[variant] = normalized
            elif isinstance(rows, list):
                self._add_rows(result, rows)
        elif isinstance(payload, list):
            self._add_rows(result, payload)
        return result

    @staticmethod
    def _add_rows(result: dict[str, list[dict[str, Any]]], rows: list[Any]) -> None:
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            key = row.get("identifier") or row.get("key") or row.get("doi") or row.get("arxiv") or row.get("pmid")
            passages = row.get("passages") or row.get("evidence") or row.get("texts")
            normalized = _normalize_evidence(passages, source="local-json")
            if key and normalized:
                for variant in _identifier_variants(key):
                    result[variant] = normalized

    def fetch_records(self, record: Any, site: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        keys = [
            str(getattr(record, "identifier", "") or ""),
            str(getattr(record, "doi", "") or ""),
            str(getattr(record, "arxiv", "") or ""),
            str(getattr(record, "pmid", "") or ""),
            str(getattr(record, "key", "") or ""),
        ]
        for key in keys:
            for variant in _identifier_variants(key):
                passages = self._data.get(variant)
                if passages:
                    return [dict(item) for item in passages]
        return []

    def fetch(self, record: Any, site: Mapping[str, Any] | None = None) -> list[str]:
        return [item["text"] for item in self.fetch_records(record, site)]


class LocalEvidenceFetcher:
    """Retrieve deterministic passages from a local text/TEI evidence corpus.

    A corpus is a directory of UTF-8 ``.txt``/``.md``/``.xml``/``.tei`` files,
    or a JSON evidence pack accepted by :class:`JsonEvidenceFetcher`.  Files
    are associated with a citation by an identifier in the filename (for
    example ``doi_10.1234_abcd.txt``), a DOI/arXiv/PMID found in the first
    8 KiB, or a JSON row's ``identifier`` field.  Plain text is split on blank
    lines and XML is reduced to TEI paragraph text.  No network request is
    made and the original relative path plus paragraph number is retained as
    the evidence locator.

    The fetcher is intentionally lexical: passages are ranked by overlap with
    the local claim, which makes offline results reproducible and auditable.
    It does not infer that an unlabelled document supports a citation.
    """

    _TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}
    _XML_SUFFIXES = {".xml", ".tei"}
    _JSON_SUFFIXES = {".json"}
    _DOI_RE = re.compile(r"(?i)10\.\d{4,9}/[-._;()/:A-Z0-9]+")
    _ARXIV_RE = re.compile(r"(?i)(?:arxiv\s*:\s*)?((?:[a-z][\w.-]*/)?\d{4}\.\d{4,5}(?:v\d+)?)")
    _PMID_RE = re.compile(r"(?i)(?:pmid\s*[:#]?\s*)(\d{1,10})")

    def __init__(self, path: str | Path, *, max_files: int = 2000, max_file_bytes: int = 20 * 1024 * 1024):
        self.path = Path(path)
        self.max_files = max(1, int(max_files))
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.is_file() and self.path.suffix.casefold() in self._JSON_SUFFIXES:
            self._data = JsonEvidenceFetcher(self.path)._data
            return
        if not self.path.is_dir():
            return
        try:
            paths = sorted(item for item in self.path.rglob("*") if item.is_file())
        except OSError:
            return
        for index, item in enumerate(paths):
            if index >= self.max_files:
                break
            suffix = item.suffix.casefold()
            try:
                if item.stat().st_size > self.max_file_bytes:
                    continue
            except OSError:
                continue
            if suffix in self._JSON_SUFFIXES:
                self._load_json(item)
            elif suffix in self._TEXT_SUFFIXES:
                self._load_text(item)
            elif suffix in self._XML_SUFFIXES:
                self._load_xml(item)

    def _load_json(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError, TypeError):
            return
        rows = payload.get("evidence", payload) if isinstance(payload, dict) else payload
        if isinstance(rows, dict):
            for key, value in rows.items():
                self._add(key, _normalize_evidence(value, source="local-json", locator=self._locator(path)))
        elif isinstance(rows, list):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                key = row.get("identifier") or row.get("key") or row.get("doi") or row.get("arxiv") or row.get("pmid")
                passages = row.get("passages") or row.get("evidence") or row.get("texts")
                self._add(key, _normalize_evidence(passages, source="local-json", locator=self._locator(path)))

    def _load_text(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return
        keys = self._file_keys(path, text[:8192])
        passages = self._split_passages(text)
        records = [
            {"text": passage, "source": "local-text", "locator": f"{self._locator(path)}#p{index}"}
            for index, passage in enumerate(passages, 1)
        ]
        for key in keys:
            self._add(key, records)

    def _load_xml(self, path: Path) -> None:
        try:
            content = path.read_bytes()
        except OSError:
            return
        if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            return
        try:
            root = ET.fromstring(content)
        except (ET.ParseError, ValueError):
            return
        nodes = [node for node in root.iter() if _xml_local(node.tag) in {"p", "ab", "head", "note"}]
        passages = [" ".join(part.strip() for part in node.itertext() if part and part.strip()) for node in nodes]
        passages = [passage for passage in passages if passage]
        if not passages:
            text = " ".join(part.strip() for part in root.itertext() if part and part.strip())
            passages = self._split_passages(text)
        header = " ".join(part.strip() for part in root.itertext() if part and part.strip())[:8192]
        records = [
            {"text": passage, "source": "local-tei", "locator": f"{self._locator(path)}#p{index}"}
            for index, passage in enumerate(passages, 1)
        ]
        for key in self._file_keys(path, header):
            self._add(key, records)

    def _add(self, key: Any, records: list[dict[str, Any]]) -> None:
        if not key or not records:
            return
        for variant in _identifier_variants(key):
            self._data.setdefault(variant, []).extend(dict(item) for item in records)

    def fetch_records(self, record: Any, site: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        keys = [
            str(getattr(record, "identifier", "") or ""),
            str(getattr(record, "doi", "") or ""),
            str(getattr(record, "arxiv", "") or ""),
            str(getattr(record, "pmid", "") or ""),
            str(getattr(record, "key", "") or ""),
        ]
        seen: set[str] = set()
        for key in keys:
            for variant in _identifier_variants(key):
                for item in self._data.get(variant, []):
                    marker = f"{item.get('locator', '')}\0{item.get('text', '')}"
                    if marker not in seen:
                        seen.add(marker)
                        candidates.append(dict(item))
        if not candidates:
            return []
        claim = str((site or {}).get("claim", "") or "")
        tokens = _evidence_tokens(claim)
        if tokens:
            candidates.sort(key=lambda item: (-_token_overlap(tokens, str(item.get("text", ""))), str(item.get("locator", ""))))
        return candidates[:8]

    def fetch(self, record: Any, site: Mapping[str, Any] | None = None) -> list[str]:
        return [item["text"] for item in self.fetch_records(record, site)]

    def _locator(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.path)).replace("\\", "/") if self.path.is_dir() else path.name
        except ValueError:
            return path.name

    @staticmethod
    def _split_passages(text: str) -> list[str]:
        parts = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"(?:\r?\n){2,}", text)]
        return [part for part in parts if part]

    def _file_keys(self, path: Path, sample: str) -> list[str]:
        keys: list[str] = []
        stem = path.stem.strip()
        doi_file = re.match(r"(?i)(?:doi[-_]?)?(10\.\d{4,9})[_-](.+)$", stem)
        if doi_file:
            prefix, suffix = doi_file.groups()
            keys.extend(_identifier_variants(f"doi:{prefix}/{suffix.replace('_', '/')}"))
        keys.extend(_identifier_variants(stem))
        for value in self._DOI_RE.findall(sample):
            keys.extend(_identifier_variants(value))
        for match in self._ARXIV_RE.finditer(sample):
            keys.extend(_identifier_variants(f"arxiv:{match.group(1)}"))
        for value in self._PMID_RE.findall(sample):
            keys.extend(_identifier_variants(f"pmid:{value}"))
        return list(dict.fromkeys(keys))


def _xml_local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1].casefold()


def _evidence_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z\u4e00-\u9fff]{3,}", str(value or "").casefold()) if token not in _STOPWORDS}


def _token_overlap(tokens: set[str], text: str) -> float:
    if not tokens:
        return 0.0
    words = _evidence_tokens(text)
    return len(tokens & words) / len(tokens)


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "were", "was", "are", "has", "have",
    "not", "into", "than", "their", "which", "本文", "研究", "结果", "方法", "以及", "通过",
}


def _normalize_evidence(
    value: Any,
    *,
    source: str,
    locator: str = "",
) -> list[dict[str, Any]]:
    """Normalize strings and passage mappings while retaining provenance."""

    if isinstance(value, str):
        text = value.strip()
        return [{"text": text, "source": source, "locator": locator}] if text else []
    if isinstance(value, Mapping):
        text_value = value.get("text") or value.get("passage") or value.get("content") or value.get("evidence")
        if not isinstance(text_value, str) or not text_value.strip():
            return []
        item: dict[str, Any] = {
            "text": text_value.strip(),
            "source": str(value.get("source") or source),
            "locator": str(value.get("locator") or value.get("location") or locator),
        }
        if value.get("confidence") is not None:
            try:
                item["confidence"] = round(max(0.0, min(1.0, float(value["confidence"]))), 4)
            except (TypeError, ValueError):
                pass
        if value.get("retrieved_at"):
            item["retrieved_at"] = str(value["retrieved_at"])
        return [item]
    if isinstance(value, (list, tuple)):
        records: list[dict[str, Any]] = []
        for item in value:
            records.extend(_normalize_evidence(item, source=source, locator=locator))
        return records
    return []


def _identifier_variants(value: Any) -> list[str]:
    """Return stable aliases for common DOI/arXiv/PMID evidence keys."""

    raw = str(value or "").strip().casefold()
    if not raw:
        return []
    variants = [raw]
    cleaned = raw.rstrip(".,;)])}")
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    if cleaned.startswith("doi:"):
        cleaned = cleaned[4:].strip()
    if cleaned.startswith("arxiv:"):
        cleaned = cleaned[6:].strip()
    if cleaned.startswith("pmid:"):
        cleaned = cleaned[5:].strip()
    if cleaned and cleaned not in variants:
        variants.append(cleaned)
    return variants


__all__ = [
    "JsonEvidenceFetcher",
    "LocalEvidenceFetcher",
    "fetch_evidence",
    "fetch_evidence_records",
    "metadata_evidence",
    "metadata_passages",
]
