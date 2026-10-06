"""Link in-text citation marks to the sentence-level claim they annotate."""

from __future__ import annotations

import re
from typing import Any


_SENTENCE_END = re.compile(r"[。！？!?；;]|(?<=[.!?])\s+")


def link_citation_claims(doc: Any, entry: Any, marks: list[Any] | None = None) -> list[dict[str, Any]]:
    """Return citation sites enriched with a conservative sentence claim.

    This is intentionally closed-book: it extracts the author's own sentence
    and never treats a bibliography title as supporting evidence.  Later
    evidence fetchers can add passages and a support judgement to each site.
    """
    entry_key = str(getattr(entry, "key", "") or "")
    entry_index = getattr(entry, "index", None)
    blocks = {str(block.id): block for block in getattr(doc, "blocks", []) or []}
    sites: list[dict[str, Any]] = []
    for mark in marks or getattr(doc, "citation_marks", []) or []:
        mark_key = str(getattr(mark, "key", "") or "")
        if not _matches(mark_key, entry_key, entry_index):
            continue
        block_id = str(getattr(mark, "block_id", "") or "")
        char_range = getattr(mark, "char_range", (0, 0))
        text = str(getattr(blocks.get(block_id), "text", "") or "")
        claim = sentence_claim(text, char_range, str(getattr(mark, "raw", "") or ""))
        sites.append(
            {
                "block_id": block_id,
                "location": block_id,
                "char_range": list(char_range) if isinstance(char_range, (tuple, list)) else [],
                "raw": str(getattr(mark, "raw", "") or ""),
                "claim": claim,
                "evidence_passages": [],
                "support_status": "uncertain",
                "support_confidence": 0.0,
            }
        )
    return sites


def sentence_claim(text: str, char_range: Any, raw: str = "") -> str:
    """Extract the smallest surrounding sentence containing a citation mark."""
    if not text:
        return ""
    try:
        start, end = int(char_range[0]), int(char_range[1])
    except (TypeError, ValueError, IndexError):
        start, end = 0, 0
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    left = 0
    right = len(text)
    for match in _SENTENCE_END.finditer(text, 0, start):
        left = match.end()
    match = _SENTENCE_END.search(text, end)
    if match:
        right = match.end()
    claim = text[left:right].strip()
    if raw:
        claim = re.sub(re.escape(raw), "", claim, count=1).strip(" \t,，;；:：")
    return claim


def _matches(mark_key: str, entry_key: str, index: int | None) -> bool:
    if index is not None:
        values: set[int] = set()
        for part in re.split(r"[,;]", mark_key):
            part = part.strip()
            range_match = re.fullmatch(r"(\d+)\s*[\-–—]\s*(\d+)", part)
            if range_match:
                left, right = int(range_match.group(1)), int(range_match.group(2))
                values.update(range(left, right + 1) if left <= right else range(right, left + 1))
            else:
                values.update(int(value) for value in re.findall(r"\d+", part))
        return index in values
    return bool(entry_key and mark_key.casefold() == entry_key.casefold())
