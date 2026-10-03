"""Deterministic coordination plan for academic review agents.

The default route is still host-side (Codex/WorkBuddy), while each role may
carry a configured model profile for an optional local API runner. Keeping the
plan in the project makes both routes auditable and prevents the workflow from
silently becoming a single general-purpose review call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import datetime, timezone


SKILLS = {
    "paper_audit": {
        "purpose": "定位、证据门禁、确定性检查、报告、无损副本修订",
        "path": "skills/paper-audit/SKILL.md",
    },
    "nature_writing": {
        "purpose": "按论文类型重建问题—gap—方法—结果—边界的论证链",
        "path": "~/.codex/skills/nature-writing/SKILL.md",
    },
    "nature_polishing": {
        "purpose": "在论证成立后做学术语言、段落职责和过度声明检查",
        "path": "~/.codex/skills/nature-polishing/SKILL.md",
    },
    "academic_humanizer": {
        "purpose": "保留数字、结果和引用，检查 claim-evidence 纪律与 AI 化表达",
        "path": "~/.agents/skills/academic-humanizer/SKILL.md",
    },
    "nature_reader": {
        "purpose": "长文分段、图表关联、页码和稳定源锚点",
        "path": "~/.agents/skills/nature-reader/SKILL.md",
    },
    "nature_citation": {
        "purpose": "引用分段、支持等级和参考文献导出；不把标题相关当作支撑",
        "path": "~/.agents/skills/nature-citation/SKILL.md",
    },
    "nature_data": {
        "purpose": "数据、代码、源数据与 Data Availability 的可复现性审查",
        "path": "~/.agents/skills/nature-data/SKILL.md",
    },
    "nature_response": {
        "purpose": "如有审稿意见，逐条映射评论、修改位置和未决输入",
        "path": "~/.agents/skills/nature-response/SKILL.md",
    },
}


ROLES = [
    {
        "id": "argument_reviewer",
        "label": "论证与贡献审查",
        "order": 1,
        "model_profile": "host_agent",
        "checklist_groups": ["structure", "argument", "consistency"],
        "skills": ["paper_audit", "nature_writing"],
        "prompt": "只审研究问题、gap、贡献、首尾呼应和过度声明；每条意见给原文引句、块 ID、严重度和可执行修改方向。",
    },
    {
        "id": "method_data_reviewer",
        "label": "方法与数据审查",
        "order": 1,
        "model_profile": "host_agent",
        "checklist_groups": ["method", "data"],
        "skills": ["paper_audit", "nature_writing", "nature_data"],
        "prompt": "检查数据来源、系统边界、假设、统计/敏感性分析、复现信息和数字一致性；不得把缺少的实验结果补写成事实。",
    },
    {
        "id": "citation_figure_reviewer",
        "label": "引用与图表审查",
        "order": 1,
        "model_profile": "host_agent",
        "checklist_groups": ["citation", "figure"],
        "skills": ["paper_audit", "nature_citation", "nature_reader"],
        "prompt": "检查引用真实性、论断—引用支撑、引用格式、图表编号/题注/正文解读；外部文献只给可核查的 DOI 或来源。",
    },
    {
        "id": "language_editor",
        "label": "语言与术语审查",
        "order": 1,
        "model_profile": "host_agent",
        "checklist_groups": ["language", "consistency"],
        "skills": ["paper_audit", "academic_humanizer", "nature_polishing"],
        "prompt": "只处理术语统一、缩写、语法、冗余、语气和 claim-evidence 表述；不得改变数字、结果、引用或科学含义。",
    },
]


def default_workflow() -> dict[str, Any]:
    """Return an editable copy of the built-in workflow contract."""
    return {
        "version": "1",
        "roles": [{**role, "enabled": True} for role in ROLES],
        "task_overrides": {},
        "feedback_edges": [],
        "canvas": {
            "start": {"label": "解析与预检", "prompt": "解析论文并生成确定性检查输入。", "position": {"x": 24, "y": 150}},
            "end": {"label": "证据门禁与报告", "prompt": "统一执行证据门禁、去重并生成审查报告。", "position": {"x": 900, "y": 150}},
        },
    }


def validate_workflow(workflow: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize a user-editable workflow specification."""
    if workflow is None:
        return default_workflow()
    if not isinstance(workflow, dict):
        raise ValueError("workflow must be an object")
    base = {role["id"]: role for role in ROLES}
    normalized_roles = []
    seen: set[str] = set()
    for index, raw in enumerate(workflow.get("roles", [])):
        if not isinstance(raw, dict):
            raise ValueError("workflow.roles entries must be objects")
        role_id = str(raw.get("id", "")).strip()
        if not role_id or role_id in seen:
            raise ValueError(f"duplicate or empty role id: {role_id}")
        seen.add(role_id)
        role = {**base.get(role_id, {}), **raw}
        role["id"] = role_id
        role["label"] = str(role.get("label", role_id)).strip()
        try:
            role["order"] = int(role.get("order", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"order must be a positive integer: {role_id}") from exc
        if role["order"] < 1:
            raise ValueError(f"order must be a positive integer: {role_id}")
        position = role.get("position", {})
        if not isinstance(position, dict):
            raise ValueError(f"position must be an object: {role_id}")
        try:
            default_x = 230 + (index % 3) * 200
            default_y = 40 + (index // 3) * 130 + (role["order"] - 1) * 120
            role["position"] = {"x": max(0, int(position.get("x", default_x))), "y": max(0, int(position.get("y", default_y)))}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"position must contain numeric x/y: {role_id}") from exc
        role["checklist_groups"] = [str(x) for x in role.get("checklist_groups", [])]
        role["skills"] = [str(x) for x in role.get("skills", [])]
        unknown = [skill for skill in role["skills"] if skill not in SKILLS]
        if unknown:
            raise ValueError(f"unknown skills for {role_id}: {unknown}")
        role["prompt"] = str(role.get("prompt", ""))
        role["model_profile"] = str(role.get("model_profile", "host_agent")).strip() or "host_agent"
        role["enabled"] = bool(role.get("enabled", True))
        role["findings_file"] = str(role.get("findings_file", f"findings.{role_id}.json"))
        findings_path = Path(role["findings_file"])
        if findings_path.is_absolute() or ".." in findings_path.parts:
            raise ValueError(f"findings_file must be relative: {role_id}")
        normalized_roles.append(role)
    if not normalized_roles:
        raise ValueError("workflow must contain at least one role")
    overrides = workflow.get("task_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("task_overrides must be an object")
    normalized_overrides = {}
    for task_id, raw in overrides.items():
        if not isinstance(raw, dict):
            raise ValueError(f"task override must be an object: {task_id}")
        if "depends_on" in raw and (not isinstance(raw["depends_on"], list) or any(not isinstance(dep, str) for dep in raw["depends_on"])):
            raise ValueError(f"depends_on must be a list of task ids: {task_id}")
        if "output" in raw:
            outputs = raw["output"] if isinstance(raw["output"], list) else [raw["output"]]
            for output in outputs:
                output_path = Path(str(output))
                if output_path.is_absolute() or ".." in output_path.parts:
                    raise ValueError(f"task output must be relative: {task_id}")
        normalized_overrides[str(task_id)] = dict(raw)
    # Legacy workflow files may still contain the former dedicated adjudicator.
    normalized_roles = [role for role in normalized_roles if role["id"] != "skeptical_adjudicator" and not role.get("is_adjudicator")]
    if not normalized_roles:
        raise ValueError("workflow must contain at least one review agent")
    canvas = workflow.get("canvas", {})
    if not isinstance(canvas, dict):
        raise ValueError("canvas must be an object")

    def normalize_canvas_node(key: str, fallback_label: str, fallback_prompt: str, fallback_position: dict[str, int]) -> dict[str, Any]:
        raw_node = canvas.get(key, {})
        if not isinstance(raw_node, dict):
            raise ValueError(f"canvas.{key} must be an object")
        position = raw_node.get("position", fallback_position)
        if not isinstance(position, dict):
            raise ValueError(f"canvas.{key}.position must be an object")
        try:
            normalized_position = {"x": max(0, int(position.get("x", fallback_position["x"]))), "y": max(0, int(position.get("y", fallback_position["y"]))) }
        except (TypeError, ValueError) as exc:
            raise ValueError(f"canvas.{key}.position must contain numeric x/y") from exc
        return {
            "label": str(raw_node.get("label", fallback_label)).strip() or fallback_label,
            "prompt": str(raw_node.get("prompt", fallback_prompt)),
            "position": normalized_position,
        }

    normalized_canvas = {
        "start": normalize_canvas_node("start", "解析与预检", "解析论文并生成确定性检查输入。", {"x": 24, "y": 150}),
        "end": normalize_canvas_node("end", "证据门禁与报告", "统一执行证据门禁、去重并生成审查报告。", {"x": max(520, 230 + len(normalized_roles) * 170), "y": 150}),
    }
    feedback_edges = workflow.get("feedback_edges", [])
    if not isinstance(feedback_edges, list):
        raise ValueError("feedback_edges must be a list")
    known_feedback_ids = {"ingest", "verify", "revise"} | {f"review:{role['id']}" for role in normalized_roles}
    normalized_feedback = []
    for raw_edge in feedback_edges:
        if not isinstance(raw_edge, dict):
            raise ValueError("feedback edge must be an object")
        source = str(raw_edge.get("source", "")).strip()
        target = str(raw_edge.get("target", "")).strip()
        if source not in known_feedback_ids or target not in known_feedback_ids or source == target:
            raise ValueError(f"unknown or self feedback edge: {source}->{target}")
        try:
            max_iterations = int(raw_edge.get("max_iterations", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("feedback max_iterations must be a positive integer") from exc
        if max_iterations < 1:
            raise ValueError("feedback max_iterations must be a positive integer")
        edge = {"source": source, "target": target, "max_iterations": max_iterations}
        if edge not in normalized_feedback:
            normalized_feedback.append(edge)
    if "adjudicate" in normalized_overrides:
        normalized_overrides.pop("adjudicate", None)
    legacy_review_ids = [f"review:{role['id']}" for role in normalized_roles]
    if isinstance(normalized_overrides.get("verify"), dict):
        deps = normalized_overrides["verify"].get("depends_on")
        if isinstance(deps, list) and "adjudicate" in deps:
            normalized_overrides["verify"]["depends_on"] = [dep for dep in deps if dep != "adjudicate"] or legacy_review_ids
    known_task_ids = {"ingest", "verify", "revise"} | set(legacy_review_ids)
    unknown_tasks = sorted(set(normalized_overrides) - known_task_ids)
    if unknown_tasks:
        raise ValueError(f"unknown task override: {unknown_tasks[0]}")
    ordered_groups: dict[int, list[str]] = {}
    for role in normalized_roles:
        ordered_groups.setdefault(role["order"], []).append(f"review:{role['id']}")
    task_graph = [{"id": "ingest", "depends_on": []}]
    previous_group = []
    for order in sorted(ordered_groups):
        group = ordered_groups[order]
        task_graph.extend({"id": task_id, "depends_on": previous_group or ["ingest"]} for task_id in group)
        previous_group = group
    task_graph.extend([
        {"id": "verify", "depends_on": previous_group},
        {"id": "revise", "depends_on": ["verify"]},
    ])
    for task in task_graph:
        if task["id"] == "verify" and task.get("depends_on") == ["adjudicate"]:
            task["depends_on"] = legacy_review_ids
        task.update(normalized_overrides.get(task["id"], {}))
        if isinstance(task.get("depends_on"), list):
            task["depends_on"] = [dep for dep in task["depends_on"] if dep != "adjudicate"] or (legacy_review_ids if task["id"] == "verify" else task["depends_on"])
    _validate_task_graph(task_graph)
    return {
        "version": str(workflow.get("version", "1")),
        "roles": normalized_roles,
        "task_overrides": normalized_overrides,
        "feedback_edges": normalized_feedback,
        "canvas": normalized_canvas,
    }


def load_workflow(path: str | Path) -> dict[str, Any]:
    import json

    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return validate_workflow(data)


def build_plan(doc_type: str, checklist: dict[str, Any] | None = None, workflow: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a serializable multi-agent plan for one prepare run."""
    workflow = validate_workflow(workflow)
    configured_roles = workflow["roles"]
    groups = []
    if checklist:
        groups = [str(g.get("id", "")) for g in checklist.get("groups", [])]
    active_roles = [role for role in configured_roles if role.get("enabled", True) and ("all" in role["checklist_groups"] or any(group in groups for group in role["checklist_groups"]))]
    ordered = sorted(active_roles, key=lambda role: (role["order"], role["id"]))
    previous_group: list[str] = []
    task_specs = []
    for order in sorted({role["order"] for role in ordered}):
        group = [role for role in ordered if role["order"] == order]
        dependencies = previous_group or ["ingest"]
        task_specs.extend({
            "id": f"review:{role['id']}",
            "kind": "host_agent",
            "depends_on": dependencies,
            "owner": role["id"],
            "model_profile": role.get("model_profile", "host_agent"),
            "order": role["order"],
            "input": ["outline.md", "context/", "deterministic.md"],
            "output": [role.get("findings_file", f"findings.{role['id']}.json")],
        } for role in group)
        previous_group = [f"review:{role['id']}" for role in group]
    tasks = [
        {
            "id": "ingest",
            "kind": "deterministic",
            "depends_on": [],
            "owner": "paperaudit",
            "output": ["manifest.json", "outline.md", "context/", "deterministic.md"],
        },
        *task_specs,
        {
            "id": "verify",
            "kind": "deterministic",
            "depends_on": previous_group,
            "owner": "paperaudit",
            "output": ["findings.json", "review.md"],
        },
        {
            "id": "revise",
            "kind": "user_confirmed_only",
            "depends_on": ["verify"],
            "owner": "paperaudit",
            "output": ["<source>_revised.docx", "regression report"],
        },
    ]
    for task in tasks:
        override = workflow["task_overrides"].get(task["id"], {})
        if isinstance(override, dict):
            task.update(override)
    _validate_task_graph(tasks)
    return {
        "version": "1",
        "doc_type": doc_type,
        "principles": [
            "agents run in ascending order; agents with the same order run in parallel",
            "deterministic checks run before semantic review",
            "evidence gate and dedupe are code-owned",
            "revision is user-confirmed and always targets a copy",
            "numbers, results, citations, and scientific claims are never invented",
        ],
        "skills": SKILLS,
        "roles": active_roles,
        "workflow": workflow,
        "feedback_edges": workflow.get("feedback_edges", []),
        "tasks": tasks,
        "finding_file_pattern": "findings.<role>.json",
        "finding_schema": {
            "checklist_id": "A04",
            "severity": "major|minor|nit",
            "block_ids": ["p_0063"],
            "verbatim_quote": "exact source substring",
            "rationale": "evidence-bound explanation",
            "suggested_fix": "optional meaning-preserving rewrite",
            "confidence": 0.8,
            "reviewer_id": "argument_reviewer",
            "needs_author_decision": True,
        },
    }


def _validate_task_graph(tasks: list[dict[str, Any]]) -> None:
    ids = {str(task.get("id", "")) for task in tasks}
    if "" in ids or len(ids) != len(tasks):
        raise ValueError("task ids must be unique and non-empty")
    graph: dict[str, list[str]] = {}
    for task in tasks:
        task_id = str(task["id"])
        deps = task.get("depends_on", [])
        if not isinstance(deps, list) or any(str(dep) not in ids for dep in deps):
            raise ValueError(f"unknown task dependency in {task_id}")
        if task_id in deps:
            raise ValueError(f"task cannot depend on itself: {task_id}")
        graph[task_id] = [str(dep) for dep in deps]
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("workflow task dependencies contain a cycle")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph[node]:
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for task_id in graph:
        visit(task_id)


def write_plan(out_dir: str | Path, doc_type: str, checklist: dict[str, Any] | None = None, workflow: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write collaboration.plan.json and a compact human-readable plan."""
    import json

    out = Path(out_dir)
    plan = build_plan(doc_type, checklist, workflow)
    (out / "collaboration.plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["# 多 Agent 学术审查计划", "", f"文档类型：`{doc_type}`", ""]
    lines.append("## Agent 执行顺序")
    for role in sorted(plan["roles"], key=lambda item: (item["order"], item["id"])):
        lines.append(f"- 顺序 {role['order']}：`{role['id']}`（{role['label']}）；清单组：{', '.join(role['checklist_groups'])}；输出：`{role['findings_file']}`")
    lines += ["", "## 门禁与依赖", "", "1. PaperAudit 先完成解析和确定性检查。", "2. Agent 按顺序号递增执行；同一顺序号的 Agent 并行读取输入并分别写 findings 文件。", "3. 所有 Agent 完成后，代码统一执行证据门禁和保守去重并生成报告。", "4. 用户确认后才允许在副本上修订，并重跑确定性检查。"]
    if plan.get("feedback_edges"):
        lines += ["5. 循环回流只按 `max_iterations` 上限重新运行目标 Agent，不改变一次性依赖图。", "", "## 循环回流", ""]
        lines.extend(f"- `{edge['source']}` → `{edge['target']}`，最多重来 {edge['max_iterations']} 次。" for edge in plan["feedback_edges"])
    lines += ["", "## Skills 路由", ""]
    for key, value in plan["skills"].items():
        lines.append(f"- `{key}`：{value['purpose']}")
    (out / "collaboration.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan


def append_trace(out_dir: str | Path, event: str, **payload: Any) -> None:
    """Append one JSONL lifecycle event without exposing manuscript text."""
    import json

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **payload,
    }
    path = Path(out_dir) / "trace.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
