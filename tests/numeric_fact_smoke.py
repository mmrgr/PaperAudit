"""Small, dependency-free smoke checks for the numeric fact graph."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper.evidence.numeric_facts import check, extract_facts  # noqa: E402
from paperrevamper.models import Block, BlockKind, DocumentIR, IssueType  # noqa: E402


def main() -> None:
    blocks = [
        Block("p_1", BlockKind.PARAGRAPH, "The accuracy was 82% in the treatment group."),
        Block("p_2", BlockKind.PARAGRAPH, "The accuracy was 75% in the treatment group."),
        Block("p_3", BlockKind.PARAGRAPH, "The result was statistically significant (p = 0.08)."),
        Block("p_4", BlockKind.PARAGRAPH, "The estimate was 2.1 (95% CI [1.2, 3.4]) and n=40."),
        Block(
            "t_1",
            BlockKind.TABLE,
            "",
            rows=[["Group", "n"], ["A", "30"], ["B", "20"], ["Total", "55"]],
        ),
        # A year and a citation index must not become metric facts.
        Block("p_5", BlockKind.PARAGRAPH, "See [3] and the 2024 survey."),
        Block("p_6", BlockKind.PARAGRAPH, "precision was 80%, recall was 60%, and F1 score was 90%."),
    ]
    doc = DocumentIR("smoke", "", "", blocks=blocks)
    facts = extract_facts(doc)
    assert any(f.metric == "accuracy" and f.value == 82 for f in facts)
    ci = next(f for f in facts if f.kind == "ci")
    assert ci.interval == (1.2, 3.4)
    assert not any(f.source_block == "p_5" for f in facts)

    findings = check(doc)
    types = [f.issue_type for f in findings]
    assert IssueType.NUMERIC_INCONSISTENCY in types
    assert IssueType.STATS_INCONSISTENCY in types
    assert any("55" in f.verbatim_quote for f in findings)
    assert any("precision/recall" in f.rationale for f in findings)
    print({"facts": len(facts), "findings": len(findings), "status": "ok"})


if __name__ == "__main__":
    main()
