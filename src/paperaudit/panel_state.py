"""Pure state aggregation and user decision persistence for the local panel."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_trace(run_dir: Path) -> list[dict]:
    path = run_dir / "trace.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except json.JSONDecodeError:
            continue
    return rows


def read_decisions(run_dir: Path) -> list[dict]:
    path = run_dir / "decisions.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except json.JSONDecodeError:
            continue
    return rows


def append_decision(run_dir: Path, finding_id: str, decision: str, reason: str, source_hash: str) -> dict:
    if decision not in {"accept", "reject", "contest"}:
        raise ValueError("decision must be accept, reject, or contest")
    row = {
        "finding_id": finding_id,
        "decision": decision,
        "actor": "user",
        "reason": reason.strip(),
        "source_hash": source_hash,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with (run_dir / "decisions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def latest_decisions(run_dir: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in read_decisions(run_dir):
        if row.get("finding_id"):
            latest[str(row["finding_id"])] = row
    return latest


def _role_state(run_dir: Path, role: dict) -> dict:
    role_id = str(role.get("id", ""))
    configured_file = role.get("findings_file") or f"findings.{role_id}.json"
    path = run_dir / str(configured_file)
    data = read_json(path, {})
    items = data.get("findings", []) if isinstance(data, dict) else data
    return {
        "id": role_id,
        "label": role.get("label", role_id),
        "order": int(role.get("order", 1)),
        "status": "completed" if path.exists() else "pending",
        "skills": role.get("skills", []),
        "checklist_groups": role.get("checklist_groups", []),
        "prompt": role.get("prompt", ""),
        "model_profile": role.get("model_profile", "host_agent"),
        "input_files": ["outline.md", "deterministic.md", "context/"],
        "output_file": path.name,
        "candidate_count": len(items) if isinstance(items, list) else 0,
    }


def build_state(run_dir: str | Path) -> dict:
    run = Path(run_dir).resolve()
    manifest = read_json(run / "manifest.json", {})
    plan = read_json(run / "collaboration.plan.json", {})
    result = read_json(run / "findings.json", {})
    trace = read_trace(run)
    decisions = latest_decisions(run)
    roles = [_role_state(run, role) for role in plan.get("roles", [])] if isinstance(plan, dict) else []
    has_manifest = (run / "manifest.json").exists()
    has_verify = (run / "findings.json").exists()
    has_apply = any(row.get("event") == "apply_complete" for row in trace)
    confirmed = list(result.get("confirmed", [])) if isinstance(result, dict) else []
    rejected = list(result.get("rejected", [])) if isinstance(result, dict) else []
    decision_values = {key: value.get("decision") for key, value in decisions.items()}
    for finding in confirmed + rejected:
        finding["panel_decision"] = decision_values.get(str(finding.get("id", "")), "pending")
    role_by_id = {str(role.get("id")): role for role in roles}
    task_labels = {"ingest": "解析", "deterministic": "确定性检查", "verify": "门禁与报告", "revise": "副本修改", "regression": "回归复验", "author_approval": "作者确认"}

    def task_status(task: dict) -> str:
        if task.get("enabled") is False:
            return "disabled"
        task_id = str(task.get("id", ""))
        if task_id == "ingest":
            return "completed" if has_manifest else "pending"
        if task_id == "deterministic":
            return "completed" if (run / "deterministic.md").exists() else "pending"
        if task_id.startswith("review:"):
            role_id = task_id.split(":", 1)[1]
            return role_by_id.get(role_id, {}).get("status", "pending")
        if task_id == "verify":
            return "completed" if has_verify else "pending"
        if task_id == "author_approval":
            return "completed" if confirmed and all(decision_values.get(str(f.get("id"))) in {"accept", "reject", "contest"} for f in confirmed) else ("waiting_user" if confirmed else "pending")
        if task_id in {"revise", "regression"}:
            return "completed" if has_apply else "pending"
        outputs = task.get("output", [])
        return "completed" if outputs and all((run / str(output)).exists() for output in outputs if str(output) != "<source>_revised.docx") else "pending"

    plan_tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
    stages = [
        {"id": str(task.get("id")), "label": task.get("label") or task_labels.get(str(task.get("id")), str(task.get("id"))), "status": task_status(task), "depends_on": task.get("depends_on", []), "order": task.get("order", "")}
        for task in plan_tasks
    ]
    if not stages:
        stages = [{"id": "ingest", "label": "解析", "status": "completed" if has_manifest else "pending", "depends_on": []}]
    artifacts = [p.name for p in sorted(run.iterdir()) if p.is_file()] if run.exists() else []
    return {
        "run_dir": str(run),
        "source": manifest.get("source", ""),
        "source_hash": manifest.get("hash", ""),
        "manifest": manifest,
        "plan": plan,
        "stages": stages,
        "roles": roles,
        "findings": {"confirmed": confirmed, "rejected": rejected},
        "decisions": decisions,
        "coverage": {"confirmed": len(confirmed), "rejected": len(rejected), "accepted": sum(v == "accept" for v in decision_values.values()), "contested": sum(v == "contest" for v in decision_values.values())},
        "artifacts": artifacts,
        "trace": trace,
        "trace_cursor": len(trace),
    }
