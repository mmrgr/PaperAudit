"""Focused checks for the two-position multi-model panel protocol."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper.panel_state import build_state
from paperrevamper.protocol.adjudication import aggregate_panel, adjudicate_run, to_markdown


def _judgment(finding_id: str, model: str, position: str, verdict: str) -> dict:
    return {
        "finding_id": finding_id,
        "judge_model": model,
        "position": position,
        "verdict": verdict,
        "raw_json": {"verdict": verdict},
    }


def main() -> int:
    findings = [{"id": "F001"}, {"id": "F002"}, {"id": "F003"}, {"id": "F004"}]
    judgments = [
        *[_judgment("F001", model, position, "yes") for model in ("model-a", "model-b") for position in ("claim_first", "evidence_first")],
        _judgment("F002", "model-a", "claim_first", "yes"),
        _judgment("F002", "model-a", "evidence_first", "yes"),
        _judgment("F002", "model-b", "claim_first", "no"),
        _judgment("F002", "model-b", "evidence_first", "no"),
        *[_judgment("F003", model, position, "cannot_assess") for model in ("model-a", "model-b") for position in ("claim_first", "evidence_first")],
        _judgment("F004", "model-a", "claim_first", "yes"),
        _judgment("F004", "model-a", "evidence_first", "yes"),
    ]
    result = aggregate_panel(findings, judgments)
    verdicts = {row["finding_id"]: row["verdict"] for row in result["findings"]}
    assert verdicts == {"F001": "confirmed", "F002": "contested", "F003": "unverifiable", "F004": "unverifiable"}
    assert result["summary"] == {"confirmed": 1, "contested": 1, "refuted": 0, "unverifiable": 2}
    assert "Panel Adjudication" in to_markdown(result)

    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "run"
        run.mkdir()
        (run / "findings.json").write_text(json.dumps({"source": "paper.docx", "confirmed": findings, "rejected": []}), encoding="utf-8")
        judgment_path = Path(tmp) / "judgments.json"
        judgment_path.write_text(json.dumps({"judgments": judgments}), encoding="utf-8")
        persisted = adjudicate_run(run, judgment_path)
        assert persisted["status"] == "ok"
        assert (run / "adjudication.json").exists()
        assert (run / "adjudication.md").exists()
        state = build_state(run)
        row = next(item for item in state["findings"]["confirmed"] if item["id"] == "F001")
        assert row["panel_verdict"] == "confirmed"
        assert state["coverage"]["panel_verdicts"]["contested"] == 1

    print({"status": "ok", "summary": result["summary"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
