"""Offline smoke checks for the provider-backed panel runner."""

from __future__ import annotations

import json
import re
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper.panel_runner import run_panel


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "run"
        run.mkdir()
        (run / "findings.json").write_text(
            json.dumps({
                "source": "paper.docx",
                "confirmed": [
                    {
                        "id": f"F{index:03d}",
                        "issue_type": "structure_issue",
                        "severity": "minor",
                        "confidence": 0.9,
                        "block_ids": ["p_0001"],
                        "verbatim_quote": "A clear sentence.",
                        "rationale": "The finding is grounded.",
                    }
                    for index in range(1, 7)
                ],
                "rejected": [],
            }),
            encoding="utf-8",
        )
        (run / "ir_snapshot.json").write_text(
            json.dumps({"blocks": [{"id": "p_0001", "text": "A clear sentence."}]}),
            encoding="utf-8",
        )
        config = run / "models.json"
        config.write_text(
            json.dumps({
                "active_profile": "judge_a",
                "profiles": [
                    {"id": "judge_a", "protocol": "openai_compatible", "model": "a", "base_url": "http://fake", "enabled": True},
                    {"id": "judge_b", "protocol": "openai_compatible", "model": "b", "base_url": "http://fake", "enabled": True},
                ],
            }),
            encoding="utf-8",
        )
        calls: list[tuple[str, str]] = []

        def fake_chat(profile, messages, timeout=120):
            user = messages[1]["content"]
            position = "evidence_first" if "当前判定位置：evidence_first" in user else "claim_first"
            calls.append((str(profile["id"]), position))
            packet = re.search(r"<untrusted_panel_packet[^>]*>(.*?)</untrusted_panel_packet", user, re.S)
            finding_ids = re.findall(r'"id":"(F\d+)"', packet.group(1) if packet else "")
            return {
                "status": "ok",
                "model": profile["model"],
                "text": json.dumps({"judgments": [
                    {
                        "finding_id": finding_id,
                        "verdict": "yes",
                        "reason": "The supplied evidence supports the finding.",
                        "extracted_claim": "The finding is grounded.",
                        "evidence_summary": "A clear sentence.",
                    }
                    for finding_id in finding_ids
                ]}),
            }

        result = run_panel(
            run,
            profile_ids=["judge_a", "judge_b"],
            config_path=config,
            chat_fn=fake_chat,
        )
        assert result["status"] == "ok"
        assert len(calls) == 8  # two bounded finding batches × two models × two positions
        assert result["adjudication"]["summary"]["confirmed"] == 6
        assert json.loads((run / "panel.judgments.json").read_text(encoding="utf-8"))["judgments"]
        assert json.loads((run / "panel.runtime.json").read_text(encoding="utf-8"))["status"] == "complete"

        resumed = run_panel(
            run,
            profile_ids=["judge_a", "judge_b"],
            config_path=config,
            chat_fn=fake_chat,
        )
        assert resumed["status"] == "ok"
        assert len(calls) == 8
        trace = [json.loads(line) for line in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        assert trace and all(isinstance(row, dict) for row in trace)

    print({"status": "ok", "calls": len(calls), "confirmed": 6, "verdict": "confirmed"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
