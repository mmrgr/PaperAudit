"""Regression corpus gate for controlled adversarial detector cases."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperaudit.benchmark import run_benchmark


def main() -> int:
    result = run_benchmark(ROOT / "benchmarks" / "adversarial.json")
    summary = result["summary"]
    assert result["status"] == "ok"
    assert summary["cases"] == 12
    assert summary["precision"] >= 1.0
    assert summary["recall"] >= 1.0
    assert summary["fpr"] == 0.0
    assert all(case["status"] == "ok" for case in result["cases"])
    assert sum(int(case.get("gate_rejected", 0)) for case in result["cases"]) == 0
    print({"status": "ok", "cases": summary["cases"], "precision": summary["precision"], "recall": summary["recall"], "fpr": summary["fpr"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
