"""Calibrated claim/evidence matching for Citation Integrity v1."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}")
_STOP = {
    "the", "and", "for", "that", "with", "from", "this", "are", "was", "were",
    "研究", "本文", "结果", "方法", "以及", "通过", "表明", "认为",
}


def judge_support(
    claim: str,
    passages: Iterable[str] | None = None,
    *,
    evidence_records: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return supported/partial/unsupported/uncertain with a calibrated score.

    Lexical overlap is only a triage signal; the result is marked ``heuristic``
    and never upgrades a citation without retrieved evidence beyond uncertain.
    When evidence records carry an explicit ``confidence`` value, low
    confidence material is prevented from becoming a ``supported`` claim even
    when its wording overlaps perfectly.  This keeps author supplied or
    weakly retrieved passages auditable without changing the compatibility
    path that accepts a plain list of strings.
    """
    claim = str(claim or "").strip()
    records = _records(passages, evidence_records)
    evidence = [item["text"] for item in records]
    if not claim or not evidence:
        return {
            "status": "uncertain",
            "confidence": 0.0,
            "evidence_confidence": 0.0,
            "method": "no-evidence",
            "evidence_passages": evidence,
        }
    claim_tokens = _tokens(claim)
    evidence_tokens = _tokens(" ".join(evidence))
    if not claim_tokens or not evidence_tokens:
        return {
            "status": "uncertain",
            "confidence": 0.0,
            "evidence_confidence": _record_confidence(records),
            "method": "no-comparable-tokens",
            "evidence_passages": evidence,
        }
    overlap = len(claim_tokens & evidence_tokens) / len(claim_tokens)
    evidence_confidence = _record_confidence(records)
    if evidence_confidence < 0.25:
        status = "uncertain"
    elif overlap >= 0.55 and evidence_confidence >= 0.5:
        status = "supported"
    elif overlap >= 0.2:
        status = "partially_supported"
    else:
        status = "unsupported"
    return {
        "status": status,
        "confidence": round(min(1.0, overlap) * evidence_confidence, 2),
        "evidence_confidence": evidence_confidence,
        "method": "lexical-overlap-heuristic",
        "calibration": "provenance-confidence",
        "evidence_passages": evidence,
    }


def _records(
    passages: Iterable[str] | None,
    evidence_records: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize the two supported input forms into text/confidence records."""

    if evidence_records is not None:
        result: list[dict[str, Any]] = []
        for value in evidence_records:
            if not isinstance(value, Mapping):
                continue
            text = str(value.get("text") or value.get("passage") or value.get("content") or "").strip()
            if not text:
                continue
            result.append({"text": text, "confidence": _confidence(value.get("confidence"))})
        return result
    return [
        {"text": text, "confidence": 1.0}
        for item in (passages or [])
        if (text := str(item).strip())
    ]


def _confidence(value: Any) -> float:
    if value in (None, ""):
        return 1.0
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 1.0


def _record_confidence(records: list[dict[str, Any]]) -> float:
    # The strongest usable passage should control a claim.  Reporting the
    # maximum avoids penalizing a high-quality full-text excerpt merely because
    # the pack also contains a lower-confidence auxiliary snippet.
    return round(max((float(item.get("confidence", 1.0)) for item in records), default=0.0), 4)


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text) if token.casefold() not in _STOP}
