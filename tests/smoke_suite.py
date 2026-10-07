"""Run the repository's dependency-light smoke suite from one entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "stage_a_regression_smoke.py",
    "p0_correctness.py",
    "revise_safety.py",
    "privacy_smoke.py",
    "numeric_fact_smoke.py",
    "finding_graph_smoke.py",
    "ir_snapshot_smoke.py",
    "checklist_registry_smoke.py",
    "venue_smoke.py",
    "citation_integrity_smoke.py",
    "local_evidence_smoke.py",
    "adjudication_smoke.py",
    "panel_runner_smoke.py",
    "runner_smoke.py",
    "jobs_persistence_smoke.py",
    "grobid_reader_smoke.py",
    "benchmark_smoke.py",
    "benchmark_regression_smoke.py",
    "llm_smoke.py",
    "llm_config_smoke.py",
    "author_year_smoke.py",
    "notes_smoke.py",
    "pdf_native_smoke.py",
    "panel_smoke.py",
)


def main() -> int:
    env = os.environ.copy()
    source = str(ROOT / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    failures: list[tuple[str, int, str]] = []
    for name in SCRIPTS:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests" / name)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            output = (result.stdout + "\n" + result.stderr).strip()
            failures.append((name, result.returncode, output[-4000:]))
            break
        print(f"PASS {name}")
    if failures:
        name, code, output = failures[0]
        print(f"FAIL {name} (exit {code})")
        if output:
            print(output)
        return code or 1
    print(f"SMOKE_PASS {len(SCRIPTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
