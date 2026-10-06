from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperaudit.benchmark import _score, render_markdown, run_benchmark


def main() -> int:
    root = ROOT
    result = run_benchmark(root / "benchmarks" / "smoke.json")
    assert result["status"] == "ok"
    assert result["summary"]["tp"] == 5
    assert result["summary"]["fp"] == 0
    assert result["summary"]["fn"] == 0
    assert result["summary"]["f1"] == 1.0
    assert result["summary"]["fpr"] == 0.0
    assert result["summary"]["gate_rejected"] == 0
    assert result["summary"]["by_issue_type"]["citation_missing"]["recall"] == 1.0
    assert result["summary"]["by_issue_type"]["numeric_inconsistency"]["precision"] == 1.0
    report = render_markdown(result)
    assert "Micro:" in report and "citation-missing-and-unused" in report
    # The broad first prediction must not consume the only eligible label for
    # the second prediction. Scoring should find the maximum valid matching.
    matching = _score(
        (
            {"issue_type": "citation_missing", "block_ids": ["p1"]},
            {"issue_type": "citation_missing", "block_ids": ["p2"]},
        ),
        [
            {"issue_type": "citation_missing", "block_ids": ["p1", "p2"]},
            {"issue_type": "citation_missing", "block_ids": ["p1"]},
        ],
    )
    assert matching["tp"] == 2 and matching["fp"] == 0 and matching["fn"] == 0
    invalid = run_benchmark({"cases": [{"id": "unknown-detector", "detectors": ["bogus"], "document": {"blocks": []}}]})
    assert invalid["status"] == "error" and invalid["errors"]
    print({"status": result["status"], "f1": result["summary"]["f1"], "cases": result["summary"]["cases"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
