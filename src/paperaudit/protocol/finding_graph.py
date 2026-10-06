"""Precision-first aggregation of reviewer findings.

The graph is deliberately conservative.  A shared ``issue_type`` is only a
candidate signal; findings need shared location and overlapping evidence text
before an edge is emitted.  This keeps two unrelated observations about the
same kind of problem from being silently merged.

The module uses only the standard library and accepts the project's
``paperaudit.models.Finding`` objects.  It does not call an LLM and does not
change a finding while building the graph.  ``aggregate_findings`` returns
copies with the aggregation fields populated, so callers can safely reuse the
raw reviewer output for an audit trail.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Iterable, Iterator, Mapping, Sequence

from paperaudit.models import Finding


RELATIONS = ("duplicate", "supports", "contradicts", "same_root_cause")
_RELATION_PRIORITY = {name: i for i, name in enumerate(RELATIONS)}
_SEVERITY = {"major": 0, "minor": 1, "nit": 2}
_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_NUMBER_RE = re.compile(r"(?<![\w])(?:\d+(?:\.\d+)?|\.\d+)(?:\s*%)?(?![\w])")

# Pairs are intentionally short and explicit.  They are used only after
# location/subject similarity has passed, so a word such as "not" by itself
# cannot create a contradiction edge.
_NEGATIVE = {
    "no",
    "not",
    "without",
    "missing",
    "absent",
    "unsupported",
    "inconsistent",
    "failed",
    "fail",
    "不",
    "未",
    "无",
    "缺失",
    "不存在",
    "不一致",
    "不显著",
    "失败",
}
_POSITIVE = {
    "yes",
    "present",
    "supported",
    "consistent",
    "significant",
    "confirmed",
    "有",
    "存在",
    "一致",
    "显著",
    "支持",
    "通过",
}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").casefold()


def _node_id(finding: Finding, index: int, used: set[str]) -> str:
    """Return a stable graph id without mutating a malformed Finding."""

    base = str(getattr(finding, "id", "") or "").strip() or f"finding-{index + 1}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}#{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _normal_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    # Keep letters and numbers, replacing punctuation with spaces.  This
    # treats ``p=0.05`` and ``p = 0.05`` alike without deleting word bounds.
    text = "".join(ch if (ch.isalnum() or ch == "_") else " " for ch in text)
    return " ".join(text.split())


def _tokens(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return {token for token in _WORD_RE.findall(text) if len(token) > 1 or "\u4e00" <= token <= "\u9fff"}


def _similarity(left: Any, right: Any) -> float:
    a, b = _normal_text(left), _normal_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    at, bt = _tokens(a), _tokens(b)
    jaccard = len(at & bt) / len(at | bt) if at and bt else 0.0
    sequence = SequenceMatcher(None, a, b, autojunk=False).ratio()
    return max(jaccard, sequence)


def _text_parts(finding: Finding) -> tuple[str, ...]:
    parts = [
        str(getattr(finding, "verbatim_quote", "") or ""),
        str(getattr(finding, "rationale", "") or ""),
    ]
    parts.extend(str(x) for x in (getattr(finding, "evidence_refs", None) or []) if x)
    return tuple(part for part in parts if _normal_text(part))


def _text_similarity(left: Finding, right: Finding) -> float:
    """Best pairwise evidence similarity, with quote weighted first."""

    lparts, rparts = _text_parts(left), _text_parts(right)
    if not lparts or not rparts:
        return 0.0
    quote = _similarity(lparts[0], rparts[0]) if lparts[0] and rparts[0] else 0.0
    rationale = _similarity(lparts[1], rparts[1]) if len(lparts) > 1 and len(rparts) > 1 else 0.0
    refs = _similarity(" ".join(lparts[2:]), " ".join(rparts[2:])) if len(lparts) > 2 and len(rparts) > 2 else 0.0
    return max(quote, 0.85 * rationale, 0.75 * refs)


def _anchor_overlap(left: Finding, right: Finding) -> float:
    lblocks = {str(x) for x in (getattr(left, "block_ids", None) or []) if x}
    rblocks = {str(x) for x in (getattr(right, "block_ids", None) or []) if x}
    lpages = {str(x) for x in (getattr(left, "page_anchors", None) or []) if x}
    rpages = {str(x) for x in (getattr(right, "page_anchors", None) or []) if x}
    lall, rall = lblocks | lpages, rblocks | rpages
    if not lall or not rall:
        return 0.0
    return len(lall & rall) / len(lall | rall)


def _evidence_overlap(left: Finding, right: Finding) -> float:
    lrefs = {_normal_text(x) for x in (getattr(left, "evidence_refs", None) or []) if _normal_text(x)}
    rrefs = {_normal_text(x) for x in (getattr(right, "evidence_refs", None) or []) if _normal_text(x)}
    if not lrefs or not rrefs:
        return 0.0
    return len(lrefs & rrefs) / len(lrefs | rrefs)


def _issue_type(finding: Finding) -> str:
    return _enum_value(getattr(finding, "issue_type", ""))


def _confidence(finding: Finding) -> float:
    try:
        return max(0.0, min(1.0, float(getattr(finding, "confidence", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def _same_subject(left: Finding, right: Finding) -> tuple[float, float, float]:
    """Return location, text, and evidence scores used by every relation."""

    return _anchor_overlap(left, right), _text_similarity(left, right), _evidence_overlap(left, right)


def _polarity(text: Any) -> tuple[bool, bool]:
    tokens = _tokens(text)
    raw = _normal_text(text)
    negative = bool(tokens & _NEGATIVE) or any(word in raw for word in ("不显著", "不一致", "缺失", "不存在"))
    positive = bool(tokens & _POSITIVE) or any(word in raw for word in ("显著", "一致", "存在", "支持"))
    # Negation scopes the following predicate.  Without this small guard the
    # token ``significant`` would make ``not significant`` look positive and
    # suppress the contradiction edge we are trying to detect.
    if any(phrase in raw for phrase in ("not significant", "not consistent", "not supported", "不显著", "不一致", "不支持")):
        positive = False
    return positive, negative


def _contradiction_score(left: Finding, right: Finding, location: float, text: float, evidence: float) -> float:
    if not location:
        return 0.0
    lall = " ".join(_text_parts(left))
    rall = " ".join(_text_parts(right))
    lp, ln = _polarity(lall)
    rp, rn = _polarity(rall)
    opposite_polarity = (lp and rn) or (ln and rp)

    # Distinct numeric observations with a shared subject are a useful but
    # still conservative contradiction signal.  Require lexical overlap so
    # two unrelated numbers in the same block do not get linked.
    lnums = {x.replace(" ", "") for x in _NUMBER_RE.findall(lall)}
    rnums = {x.replace(" ", "") for x in _NUMBER_RE.findall(rall)}
    numeric_conflict = bool(lnums and rnums and lnums.isdisjoint(rnums) and text >= 0.38)
    if not opposite_polarity and not numeric_conflict:
        return 0.0
    if text < 0.28 and evidence < 0.25:
        return 0.0
    score = 0.72 + 0.16 * location + 0.08 * max(text, evidence)
    return min(0.99, score)


@dataclass(frozen=True)
class RelationEdge:
    """One undirected relationship, stored in deterministic input order."""

    source: str
    target: str
    relation: str
    confidence: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "confidence": round(float(self.confidence), 4),
            "reason": self.reason,
        }

    def __getitem__(self, key: str) -> Any:
        """Allow lightweight dict-style access at JSON/API boundaries."""

        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


class FindingGraph(Mapping[str, Any]):
    """Mapping-compatible graph result with convenient object attributes."""

    def __init__(
        self,
        findings: Sequence[Finding],
        node_ids: Sequence[str],
        edges: Sequence[RelationEdge],
        duplicate_clusters: Sequence[Sequence[str]],
    ) -> None:
        self.findings = tuple(findings)
        self.node_ids = tuple(node_ids)
        self.nodes = {node_id: finding for node_id, finding in zip(node_ids, findings)}
        self.edges = tuple(edges)
        self.duplicate_clusters = tuple(tuple(cluster) for cluster in duplicate_clusters)
        self._payload = {
            "nodes": self.nodes,
            "edges": list(self.edges),
            "relations": list(self.edges),
            "duplicate_clusters": [list(x) for x in self.duplicate_clusters],
            # Alias retained for callers that use the short graph vocabulary.
            "clusters": [list(x) for x in self.duplicate_clusters],
            "duplicates": [list(x) for x in self.duplicate_clusters],
        }

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    @property
    def relations(self) -> tuple[RelationEdge, ...]:
        return self.edges

    @property
    def duplicates(self) -> tuple[tuple[str, ...], ...]:
        return self.duplicate_clusters

    @property
    def duplicate_edges(self) -> tuple[RelationEdge, ...]:
        return tuple(edge for edge in self.edges if edge.relation == "duplicate")

    @property
    def support_edges(self) -> tuple[RelationEdge, ...]:
        return tuple(edge for edge in self.edges if edge.relation == "supports")

    @property
    def contradiction_edges(self) -> tuple[RelationEdge, ...]:
        return tuple(edge for edge in self.edges if edge.relation == "contradicts")

    @property
    def root_cause_edges(self) -> tuple[RelationEdge, ...]:
        return tuple(edge for edge in self.edges if edge.relation == "same_root_cause")

    def edges_for(self, node_id: str, relation: str | None = None) -> list[RelationEdge]:
        return [
            edge
            for edge in self.edges
            if (edge.source == node_id or edge.target == node_id)
            and (relation is None or edge.relation == relation)
        ]

    def relation_between(self, left: str, right: str) -> str | None:
        for edge in self.edges:
            if {edge.source, edge.target} == {left, right}:
                return edge.relation
        return None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "nodes": {key: value.to_dict() if hasattr(value, "to_dict") else value for key, value in self.nodes.items()},
            "edges": [edge.to_dict() for edge in self.edges],
            "duplicate_clusters": [list(x) for x in self.duplicate_clusters],
        }
        payload["relations"] = list(payload["edges"])
        payload["duplicates"] = [list(x) for x in self.duplicate_clusters]
        return payload


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            nxt = self.parent[item]
            self.parent[item] = root
            item = nxt
        return root

    def union(self, left: str, right: str) -> None:
        lroot, rroot = self.find(left), self.find(right)
        if lroot != rroot:
            self.parent[rroot] = lroot


def _edge_for_pair(left: Finding, right: Finding, left_id: str, right_id: str) -> RelationEdge | None:
    location, text, evidence = _same_subject(left, right)
    same_type = _issue_type(left) == _issue_type(right)
    confidence = min(_confidence(left), _confidence(right))

    # A polarity conflict must win over duplicate similarity.  Two reviewers
    # can quote the same sentence while reaching opposite judgements in their
    # rationales; silently collapsing those observations would lose the most
    # useful signal in the graph.
    contradiction = _contradiction_score(left, right, location, text, evidence)
    if contradiction and (same_type or text >= 0.45 or evidence >= 0.4):
        return RelationEdge(left_id, right_id, "contradicts", contradiction, "shared location with opposite evidence")

    # Duplicate requires either exact evidence text plus a shared anchor, or
    # very high similarity at the same location.  This is the key precision
    # guard: issue_type alone can never produce a duplicate edge.
    exact_quote = bool(_normal_text(getattr(left, "verbatim_quote", ""))) and _normal_text(getattr(left, "verbatim_quote", "")) == _normal_text(getattr(right, "verbatim_quote", ""))
    exact_rationale = bool(_normal_text(getattr(left, "rationale", ""))) and _normal_text(getattr(left, "rationale", "")) == _normal_text(getattr(right, "rationale", ""))
    duplicate = same_type and (
        (location > 0.0 and exact_quote)
        or (location > 0.0 and text >= 0.93 and confidence >= 0.55)
        or (location > 0.0 and exact_rationale and evidence >= 0.25)
    )
    if duplicate:
        score = max(0.94, 0.82 + 0.12 * max(location, text, evidence))
        return RelationEdge(left_id, right_id, "duplicate", min(0.999, score), "same issue, location, and evidence")

    # Support is only inferred for same-type observations with a shared
    # anchor/evidence.  Keep the threshold below duplicate but above casual
    # word overlap; a different issue type is represented by root-cause edges.
    if same_type and location > 0.0 and confidence >= 0.55:
        support_signal = max(text, evidence)
        if 0.45 <= support_signal < 0.93:
            score = min(0.92, 0.58 + 0.28 * support_signal + 0.1 * location)
            return RelationEdge(left_id, right_id, "supports", score, "same issue and complementary evidence")

    # Root cause is deliberately cross-type and location-aware.  Requiring a
    # shared evidence reference or meaningful rationale overlap avoids joining
    # every finding emitted for the same paragraph.
    if not same_type and location > 0.0 and (text >= 0.42 or evidence >= 0.5):
        score = min(0.9, 0.54 + 0.25 * max(text, evidence) + 0.12 * location)
        return RelationEdge(left_id, right_id, "same_root_cause", score, "different issue types share location and cause evidence")
    return None


def build_graph(findings: Iterable[Finding], *, min_confidence: float = 0.0) -> FindingGraph:
    """Build a deterministic, precision-first relationship graph.

    ``min_confidence`` is an optional input gate.  Findings below it remain
    nodes but do not create inferred edges, preserving them for reporting.
    """

    items = list(findings)
    used: set[str] = set()
    ids = [_node_id(finding, index, used) for index, finding in enumerate(items)]
    edges: list[RelationEdge] = []
    union = _UnionFind(ids)
    for i, left in enumerate(items):
        for j in range(i + 1, len(items)):
            right = items[j]
            if _confidence(left) < min_confidence or _confidence(right) < min_confidence:
                continue
            edge = _edge_for_pair(left, right, ids[i], ids[j])
            if edge is None:
                continue
            edges.append(edge)
            if edge.relation == "duplicate":
                union.union(edge.source, edge.target)

    groups: dict[str, list[str]] = {}
    for node_id in ids:
        groups.setdefault(union.find(node_id), []).append(node_id)
    clusters = [group for group in groups.values() if len(group) > 1]
    return FindingGraph(items, ids, edges, clusters)


def _reviewer_ids(finding: Finding) -> list[str]:
    values: list[str] = []
    for value in [getattr(finding, "reviewer_id", ""), *(getattr(finding, "reviewer_ids", None) or [])]:
        value = str(value or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _clone(finding: Finding) -> Finding:
    return deepcopy(finding)


def aggregate_findings(
    findings: Iterable[Finding] | FindingGraph,
    graph: FindingGraph | None = None,
    *,
    in_place: bool = False,
) -> list[Finding]:
    """Collapse duplicate components and populate aggregation metadata.

    The returned list contains one representative per duplicate component,
    preserving first-seen order.  ``support`` is the number of duplicate
    observations plus independent ``supports`` edges; reviewer IDs and
    related IDs are unioned deterministically.  Set ``in_place=True`` to
    write the same metadata onto the original representative objects.
    """

    if isinstance(findings, FindingGraph):
        if graph is None:
            graph = findings
        items = list(findings.findings)
    else:
        items = list(findings)
    if graph is None:
        graph = build_graph(items)
    if len(items) != len(graph.findings):
        # A graph built from a different sequence is unsafe to apply.  Rebuild
        # rather than silently associating IDs with the wrong findings.
        graph = build_graph(items)

    id_to_finding = dict(zip(graph.node_ids, items))
    id_to_index = {node_id: index for index, node_id in enumerate(graph.node_ids)}
    membership: dict[str, list[str]] = {node_id: [node_id] for node_id in graph.node_ids}
    for cluster in graph.duplicate_clusters:
        for node_id in cluster:
            membership[node_id] = list(cluster)

    emitted: set[str] = set()
    result: list[Finding] = []
    for node_id in graph.node_ids:
        if node_id in emitted:
            continue
        cluster = membership[node_id]
        emitted.update(cluster)
        members = [id_to_finding[item] for item in cluster]
        representative = min(
            members,
            key=lambda item: (
                -_confidence(item),
                _SEVERITY.get(_enum_value(getattr(item, "severity", "")), 9),
                id_to_index.get(str(getattr(item, "id", "")), 10**9),
            ),
        )
        output = representative if in_place else _clone(representative)

        reviewer_ids: list[str] = []
        for member in members:
            for reviewer in _reviewer_ids(member):
                if reviewer not in reviewer_ids:
                    reviewer_ids.append(reviewer)
        peers = [
            edge
            for edge in graph.edges
            if edge.source in cluster or edge.target in cluster
        ]
        related: list[str] = []
        for edge in peers:
            other = edge.target if edge.source in cluster else edge.source
            if other not in related:
                related.append(other)
        duplicate_ids = [item for item in cluster if item != node_id]
        support_edges = sum(1 for edge in peers if edge.relation == "supports")
        output.support = max(1, len(cluster) + support_edges)
        output.reviewer_ids = reviewer_ids
        output.related = related
        if peers:
            # Duplicate wins for a collapsed component; otherwise use the
            # strongest relation in deterministic priority order.
            output.relation = min(
                (edge.relation for edge in peers),
                key=lambda relation: _RELATION_PRIORITY.get(relation, 99),
            )
        elif duplicate_ids:
            output.relation = "duplicate"
        else:
            output.relation = ""
        result.append(output)
    return result


__all__ = [
    "RELATIONS",
    "FindingGraph",
    "RelationEdge",
    "aggregate_findings",
    "build_graph",
]
