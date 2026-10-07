"""Data structures used by the Citation Integrity v1 subsystem.

The package deliberately keeps these objects independent from the rest of
PaperRevamper.  ``CitationRecord`` can therefore be built from the existing
``DocumentIR`` or from a small, test-friendly reference object.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CitationRecord:
    """A bibliography entry together with its in-text citation occurrences."""

    raw: str
    style: str
    doi: str = ""
    arxiv: str = ""
    pmid: str = ""
    metadata_status: str = "unresolved"
    publication_status: str = "unknown"
    citation_sites: list[dict[str, Any]] = field(default_factory=list)

    # Position and parsed fields make reports useful while keeping the public
    # fields above compatible with the v1 design brief.
    key: str = ""
    index: int | None = None
    block_id: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    mismatch_fields: list[str] = field(default_factory=list)
    metadata_mismatch: bool = False

    @property
    def identifier(self) -> str:
        if self.doi:
            return f"doi:{self.doi}"
        if self.arxiv:
            return f"arxiv:{self.arxiv}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        return self.key or self.raw

    @property
    def cited(self) -> bool:
        return bool(self.citation_sites)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"identifier": self.identifier, "cited": self.cited}


@dataclass(frozen=True)
class ParsedReference:
    """Conservative fields extracted from one bibliography string."""

    title: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None


def metadata_from_payload(payload: Any) -> dict[str, Any] | None:
    """Normalize common resolver response shapes.

    Resolvers may return a metadata mapping directly or wrap it in a
    ``metadata``/``data`` property.  Unknown values are treated as unresolved
    by the caller, which is safer than guessing that a malformed response was
    authoritative.
    """

    if payload is None:
        return None
    if isinstance(payload, CitationRecord):
        return dict(payload.metadata)
    if not isinstance(payload, dict):
        return None
    nested = payload.get("metadata")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key in ("status", "metadata_status", "publication_status", "source"):
            if key in payload and key not in merged:
                merged[key] = payload[key]
        return merged
    nested = payload.get("data")
    if isinstance(nested, dict) and any(k in nested for k in ("title", "DOI", "doi", "authors")):
        return dict(nested)
    return dict(payload)

