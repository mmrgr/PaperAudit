"""Metadata resolvers for Citation Integrity v1.

The default resolver is offline and deterministic.  Network resolvers are
opt-in and swallow transport/JSON errors, because a citation audit should
still produce a useful unresolved report when a service is unavailable.
"""

from __future__ import annotations

import json
import html
import re
from difflib import SequenceMatcher
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class DeterministicResolver:
    """Offline resolver used by default.

    It intentionally never claims that a citation is externally verified.
    Returning a structured unresolved response makes the offline policy
    explicit and gives callers a stable cache value.
    """

    online = False

    def resolve(self, record: Any) -> dict[str, Any]:
        return {"status": "unresolved", "source": "offline", "metadata": {}}

    def __call__(self, record: Any) -> dict[str, Any]:
        return self.resolve(record)


class _HttpResolver:
    online = True

    def __init__(self, *, timeout: float = 5.0, user_agent: str = "PaperAudit/0.2"):
        self.timeout = max(0.1, float(timeout))
        self.user_agent = user_agent

    def _get_json(self, url: str) -> dict[str, Any] | None:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return payload if isinstance(payload, dict) else None
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            return None


class CrossrefResolver(_HttpResolver):
    """Resolve DOI or conservative bibliographic metadata through Crossref."""

    def resolve(self, record: Any) -> dict[str, Any] | None:
        doi = getattr(record, "doi", "") or (record.get("doi", "") if isinstance(record, dict) else "")
        if doi:
            url = "https://api.crossref.org/works/" + urllib.parse.quote(str(doi), safe="")
            payload = self._get_json(url)
            item = payload.get("message") if isinstance(payload, dict) else None
            if isinstance(item, dict):
                return {"status": "resolved", "source": "crossref", "metadata": _crossref_metadata(item)}
            return {"status": "unresolved", "source": "crossref", "metadata": {}}

        # Author-year and GB/T references often omit DOI.  A conservative
        # bibliographic search closes that gap without claiming a match from a
        # title alone: the result must clear a title similarity threshold and,
        # when available, agree on publication year.
        title = str(getattr(record, "title", "") or (record.get("title", "") if isinstance(record, dict) else "")).strip()
        if not title:
            return {"status": "unresolved", "source": "crossref-search", "metadata": {}}
        query = urllib.parse.urlencode({"query.bibliographic": title, "rows": "5"})
        payload = self._get_json("https://api.crossref.org/works?" + query)
        reference_year = getattr(record, "year", None) or (record.get("year") if isinstance(record, dict) else None)
        item, score = _best_crossref_match(payload, title, reference_year)
        if item is None or score < 0.84:
            return {"status": "unresolved", "source": "crossref-search", "metadata": {}}
        metadata = _crossref_metadata(item)
        metadata["match_score"] = round(score, 4)
        return {"status": "resolved", "source": "crossref-search", "metadata": metadata}

    __call__ = resolve


def _crossref_metadata(item: dict[str, Any]) -> dict[str, Any]:
    authors: list[str] = []
    for author in item.get("author", []) or []:
        if not isinstance(author, dict):
            continue
        name = " ".join(str(author.get(k, "")).strip() for k in ("given", "family") if author.get(k))
        if name:
            authors.append(name)
    date = item.get("published-print") or item.get("published-online") or item.get("issued") or {}
    parts = date.get("date-parts", []) if isinstance(date, dict) else []
    year = parts[0][0] if parts and parts[0] else None
    return {
        "title": (item.get("title") or [""])[0],
        "abstract": _clean_abstract(item.get("abstract", "")),
        "authors": authors,
        "year": year,
        "doi": item.get("DOI", ""),
        "type": item.get("type", ""),
        "update-to": item.get("update-to", []),
        "relation": item.get("relation", {}),
    }


def _best_crossref_match(payload: Any, title: str, reference_year: Any = None) -> tuple[dict[str, Any] | None, float]:
    if not isinstance(payload, dict):
        return None, 0.0
    message = payload.get("message")
    items = message.get("items", []) if isinstance(message, dict) else []
    if not isinstance(items, list):
        return None, 0.0
    best: tuple[dict[str, Any] | None, float] = (None, 0.0)
    target = _normalize_title(title)
    wanted_year = _year_value(reference_year)
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate_title = str((item.get("title") or [""])[0] if isinstance(item.get("title"), list) else item.get("title", ""))
        score = SequenceMatcher(a=target, b=_normalize_title(candidate_title)).ratio()
        if wanted_year is not None:
            candidate_year = _year_value(_published_year(item))
            if candidate_year is not None and candidate_year != wanted_year:
                score -= 0.20
        if score > best[1]:
            best = (item, max(0.0, score))
    return best


def _normalize_title(value: Any) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(value or "").casefold()).strip()


def _published_year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued"):
        value = item.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts", [])
        if parts and isinstance(parts[0], list) and parts[0]:
            return _year_value(parts[0][0])
    return None


def _year_value(value: Any) -> int | None:
    match = re.search(r"(?:18|19|20|21)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


class SemanticScholarResolver(_HttpResolver):
    """Optional public Semantic Scholar resolver.

    It queries only stable identifiers (DOI, arXiv, PMID); no API key is
    required.  A failed request returns an unresolved response.
    """

    def resolve(self, record: Any) -> dict[str, Any] | None:
        doi = getattr(record, "doi", "")
        arxiv = getattr(record, "arxiv", "")
        pmid = getattr(record, "pmid", "")
        identifier = f"DOI:{doi}" if doi else (f"ARXIV:{arxiv}" if arxiv else (f"PMID:{pmid}" if pmid else ""))
        if not identifier:
            return {"status": "unresolved", "source": "semanticscholar", "metadata": {}}
        encoded = urllib.parse.quote(identifier, safe="")
        url = f"https://api.semanticscholar.org/graph/v1/paper/{encoded}?fields=title,abstract,authors,year,externalIds,publicationTypes"
        payload = self._get_json(url)
        if not payload or not payload.get("title"):
            return {"status": "unresolved", "source": "semanticscholar", "metadata": {}}
        authors = [a.get("name", "") for a in payload.get("authors", []) if isinstance(a, dict) and a.get("name")]
        external = payload.get("externalIds") or {}
        return {
            "status": "resolved",
            "source": "semanticscholar",
            "metadata": {
                "title": payload.get("title", ""),
                "abstract": str(payload.get("abstract") or "").strip(),
                "authors": authors,
                "year": payload.get("year"),
                "doi": external.get("DOI", ""),
                "arxiv": external.get("ArXiv", ""),
                "pmid": external.get("PubMed", ""),
                "publicationTypes": payload.get("publicationTypes", []),
            },
        }

    __call__ = resolve


def _clean_abstract(value: Any) -> str:
    """Normalize Crossref's occasional JATS/XML abstract into plain text."""

    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())
