"""Dependency-free smoke checks for the precision-first Finding Graph."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper.models import Finding, IssueType, Severity  # noqa: E402
from paperrevamper.protocol.finding_graph import aggregate_findings, build_graph  # noqa: E402


def finding(
    id: str,
    issue_type: IssueType,
    quote: str,
    rationale: str,
    *,
    block: str = "p_1",
    reviewer: str = "",
    confidence: float = 0.9,
) -> Finding:
    return Finding(
        id=id,
        issue_type=issue_type,
        severity=Severity.MAJOR,
        confidence=confidence,
        block_ids=[block],
        verbatim_quote=quote,
        rationale=rationale,
        evidence_refs=[quote],
        reviewer_id=reviewer,
        source="deterministic",
    )


def main() -> None:
    duplicate_a = finding(
        "F1", IssueType.NUMERIC_INCONSISTENCY, "accuracy was 82%", "accuracy appears as 82% and is significant", reviewer="r1"
    )
    duplicate_b = finding(
        "F2", IssueType.NUMERIC_INCONSISTENCY, "Accuracy was 82%", "Accuracy appears as 82%", reviewer="r2"
    )
    unrelated_same_type = finding(
        "F3", IssueType.NUMERIC_INCONSISTENCY, "accuracy was 64%", "a different experiment reports 64%", block="p_9", reviewer="r3"
    )
    supporting = finding(
        "F4", IssueType.NUMERIC_INCONSISTENCY, "accuracy was 82% in the table", "the table repeats the accuracy observation", reviewer="r4"
    )
    contradictory = finding(
        "F5", IssueType.NUMERIC_INCONSISTENCY, "accuracy was not significant", "accuracy was not significant", reviewer="r5"
    )
    root_cause = finding(
        "F6", IssueType.CITATION_MISSING, "accuracy was 82%", "the accuracy claim lacks a citation", reviewer="r6"
    )

    graph = build_graph([duplicate_a, duplicate_b, unrelated_same_type, supporting, contradictory, root_cause])
    assert graph.relation_between("F1", "F2") == "duplicate"
    assert graph.relation_between("F1", "F3") is None, "same issue type alone must not merge"
    assert graph.relation_between("F1", "F4") in {"supports", "duplicate"}
    assert graph.relation_between("F1", "F5") == "contradicts"
    assert graph.relation_between("F1", "F6") == "same_root_cause"
    assert graph["edges"]

    aggregated = aggregate_findings([duplicate_a, duplicate_b, unrelated_same_type, supporting, contradictory, root_cause], graph)
    by_id = {item.id: item for item in aggregated}
    assert len(aggregated) == 5, "only the exact duplicate pair should collapse"
    assert by_id["F1"].support >= 2
    assert set(by_id["F1"].reviewer_ids) >= {"r1", "r2"}
    assert {"F2", "F4", "F5", "F6"}.issubset(set(by_id["F1"].related))
    print({"nodes": len(graph.nodes), "edges": len(graph.edges), "aggregated": len(aggregated), "status": "ok"})


if __name__ == "__main__":
    main()
