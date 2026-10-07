"""PaperAudit core schemas.

Five groups, deliberately kept dependency-free:
  1. DocumentIR      -- positioning ground truth
  2. Finding         -- where/why, never how-to-fix
  3. EditProposal    -- produced only in stage 2
  4. VerificationResult -- judgement, not single-LLM opinion
  5. TaskNode        -- parallel scheduling unit
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to a single space. Used by the evidence gate."""
    return _WS.sub(" ", text or "").strip()


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def stable_finding_uid(
    issue_type: Any,
    checklist_id: Any,
    block_ids: Any,
    verbatim_quote: Any,
    *,
    char_ranges: Any = (),
    page_anchors: Any = (),
    semantic_anchor: Any = "",
) -> str:
    """Return the identity of a finding independent of its display number.

    Display IDs (``F001``) are intentionally assigned after sorting and may
    change when a new issue is discovered.  Decisions and revision artifacts
    need an identity that survives that reordering, so the hash is based on
    the issue kind and its manuscript anchors instead.
    """

    issue = str(getattr(issue_type, "value", issue_type) or "").strip().casefold()
    checklist = normalize_ws(str(checklist_id or "")).casefold()
    blocks = ",".join(sorted({str(value).strip() for value in (block_ids or ()) if str(value).strip()}))
    ranges = ",".join(
        f"{int(value[0])}:{int(value[1])}"
        for value in sorted(
            (
                value
                for value in (char_ranges or ())
                if isinstance(value, (list, tuple)) and len(value) == 2
            ),
            key=lambda item: (int(item[0]), int(item[1])),
        )
    )
    pages = ",".join(sorted({str(value).strip() for value in (page_anchors or ()) if str(value).strip()}))
    quote = normalize_ws(str(verbatim_quote or ""))
    anchor = normalize_ws(str(semantic_anchor or "")) if not (blocks or ranges or pages) else ""
    material = "\x1f".join((issue, checklist, blocks, ranges, pages, quote, anchor))
    return "fd_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------
# 1. DocumentIR
# --------------------------------------------------------------------------


class BlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    CAPTION = "caption"
    LIST_ITEM = "list_item"
    BIBLIO_ENTRY = "biblio_entry"


@dataclass
class Block:
    """Smallest addressable unit. Anchors are (block_id, char_range)."""

    id: str
    kind: BlockKind
    text: str
    section_path: list[str] = field(default_factory=list)
    style_name: str = ""
    heading_level: int = 0
    is_bibliography: bool = False
    # table payload
    rows: list[list[str]] = field(default_factory=list)
    # Native PDF anchors.  DOCX blocks leave these unset; PDF readers may
    # populate page/bounding-box coordinates without changing block IDs.
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None

    @property
    def length(self) -> int:
        return len(self.text)

    def slice_text(self, start: int, end: int) -> str:
        return self.text[start:end]


@dataclass
class FigureRef:
    """A figure/table caption discovered in the document."""

    kind: str  # figure | table
    label: str  # e.g. "3"
    caption: str
    block_id: str


@dataclass
class CitationEntry:
    """One entry of the reference list."""

    index: int | None
    key: str  # "[3]" or "Smith2020"
    raw: str
    block_id: str


@dataclass
class CitationMark:
    """An in-text citation occurrence."""

    raw: str  # "[3]" / "(Smith et al., 2020)" / "Smith et al. (2020)"
    key: str  # normalized
    block_id: str
    char_range: tuple[int, int]


@dataclass
class NumericEntity:
    """A number worth cross-checking across the manuscript."""

    raw: str
    value: float
    unit: str  # %, points, raw
    context: str
    block_id: str
    char_range: tuple[int, int]


@dataclass
class DocumentIR:
    doc_id: str
    source_path: str
    source_hash: str
    blocks: list[Block] = field(default_factory=list)
    figures: list[FigureRef] = field(default_factory=list)
    citations: list[CitationEntry] = field(default_factory=list)
    citation_marks: list[CitationMark] = field(default_factory=list)
    numerics: list[NumericEntity] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def block_by_id(self, block_id: str) -> Block | None:
        for b in self.blocks:
            if b.id == block_id:
                return b
        return None

    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text)

    def word_count(self) -> int:
        return sum(len(b.text.split()) for b in self.blocks)


# --------------------------------------------------------------------------
# 2. Finding
# --------------------------------------------------------------------------


class Severity(str, Enum):
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


class Verdict(str, Enum):
    """裁决四态（PLAN_v2 §4.1）。只有 CONFIRMED 进入最终报告的确认问题区。

    M1 阶段由证据门禁产出 CONFIRMED / UNVERIFIABLE 两态；
    M4 引入裁决 panel 后补充 CONTESTED / REFUTED。
    """

    CONFIRMED = "confirmed"
    CONTESTED = "contested"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"


class IssueType(str, Enum):
    CITATION_MISSING = "citation_missing"
    CITATION_UNUSED = "citation_unused"
    CITATION_MISMATCH = "citation_mismatch"
    CITATION_NUMBERING = "citation_numbering"
    CITATION_UNSUPPORTED = "citation_unsupported"
    NUMERIC_INCONSISTENCY = "numeric_inconsistency"
    STATS_INCONSISTENCY = "stats_inconsistency"
    CROSSREF_BROKEN = "crossref_broken"
    TERMINOLOGY_UNDEFINED = "terminology_undefined"
    TERMINOLOGY_INCONSISTENT = "terminology_inconsistent"
    STRUCTURE_ISSUE = "structure_issue"
    ARGUMENT_GAP = "argument_gap"
    METHOD_GAP = "method_gap"
    REPRODUCIBILITY_GAP = "reproducibility_gap"
    CLARITY = "clarity"
    OVERCLAIM = "overclaim"
    PRIVACY_RISK = "privacy_risk"
    OTHER = "other"


@dataclass
class Finding:
    """States that something is wrong and why. Never proposes the edit."""

    id: str
    issue_type: IssueType
    severity: Severity
    confidence: float  # 0..1
    block_ids: list[str] = field(default_factory=list)
    verbatim_quote: str = ""
    rationale: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    checklist_id: str = ""
    reviewer_id: str = ""
    source: str = "llm"  # deterministic | llm
    needs_author_decision: bool = False
    # Optional proposal text is retained for compatibility with the current
    # CLI. It is not used by the evidence gate and should become an
    # EditProposal in the revision stage.
    suggested_fix: str = ""
    reviewer_ids: list[str] = field(default_factory=list)
    page_anchors: list[str] = field(default_factory=list)
    char_ranges: list[tuple[int, int]] = field(default_factory=list)
    owner_skill: str = "paper_audit"
    # adjudication state
    verdict: Verdict = Verdict.CONFIRMED
    # set by the evidence gate
    gate_passed: bool = False
    gate_reason: str = ""
    # set by aggregation
    support: int = 1
    related: list[str] = field(default_factory=list)
    relation: str = ""  # duplicate | supports | same_root_cause | contradicts
    # Stable identity for author decisions and revision tracking.  ``id`` is a
    # presentation number and may be re-assigned after sorting.
    uid: str = ""
    # Deterministic detectors may intentionally cite several independent
    # snippets.  LLM findings keep the strict contiguous default.
    quote_mode: str = "contiguous"

    def __post_init__(self) -> None:
        if not self.uid:
            self.uid = stable_finding_uid(
                self.issue_type,
                self.checklist_id,
                self.block_ids,
                self.verbatim_quote,
                char_ranges=self.char_ranges,
                page_anchors=self.page_anchors,
                semantic_anchor=self.rationale,
            )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["issue_type"] = self.issue_type.value
        d["severity"] = self.severity.value
        return d


# --------------------------------------------------------------------------
# 3. EditProposal
# --------------------------------------------------------------------------


class EditLevel(str, Enum):
    A_SAFE = "A_safe"  # spelling, format, cross-reference, terminology
    B_MEANING = "B_meaning_preserving"
    C_SUBSTANTIVE = "C_substantive"  # never auto-applied


@dataclass
class EditProposal:
    id: str
    finding_ids: list[str]
    target_blocks: list[str]
    edit_type: str
    level: EditLevel
    old_text: str
    new_text: str
    rationale: str = ""
    risk_flags: list[str] = field(default_factory=list)
    # Optimistic-lock precondition.  ``old_text`` remains for API compatibility;
    # new callers should set this field explicitly when proposing a patch.
    expected_old_text: str = ""

    def __post_init__(self) -> None:
        if not self.expected_old_text:
            self.expected_old_text = self.old_text

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["level"] = self.level.value
        return d


# --------------------------------------------------------------------------
# 4. VerificationResult
# --------------------------------------------------------------------------


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerificationResult:
    proposal_id: str
    gate_results: list[GateResult] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gate_results) and not self.regressions

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "passed": self.passed,
            "gates": [asdict(g) for g in self.gate_results],
            "regressions": self.regressions,
        }


# --------------------------------------------------------------------------
# 5. TaskNode
# --------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskNode:
    id: str
    kind: str
    depends_on: list[str] = field(default_factory=list)
    cache_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "depends_on": self.depends_on,
            "error": self.error,
        }
